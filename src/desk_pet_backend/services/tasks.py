"""음성·텍스트 요청 하나를 처리하는 흐름과 정책 (contracts § 3). 담당: backend.

Eureka는 eureka.py를 통해서만, 저장은 db.py를 통해서만 접근한다.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from time import perf_counter

from desk_pet_backend import db
from desk_pet_backend.config import settings
from desk_pet_backend.eureka import KST, get_eureka
from desk_pet_backend.schemas import (
    Decision,
    DeviceTask,
    DeviceTaskList,
    Lcd,
    Output,
    Task,
    VoiceResponse,
)
from desk_pet_backend.services import ai, task_cache

log = logging.getLogger(__name__)
HANDLE_TIMEOUT_SECONDS = 7


async def list_device_tasks() -> DeviceTaskList:
    """Pi 알람에 쓸 미완료 업무를 마감순으로 돌려준다. 알림 간격이 저장되지 않은 업무는 기본값을 쓴다.

    언제 울릴지는 Pi가 판단하므로 마감이 지난 업무도 거르지 않는다 (contracts § 2).
    """
    open_tasks = await task_cache.get_open_tasks(get_eureka())
    reminders = db.get_reminders()
    return DeviceTaskList(
        tasks=[
            DeviceTask(
                item_id=task.item_id,
                name=task.name,
                due_at=task.due_at,
                offset_minutes=reminders.get(task.item_id, settings.default_reminder_minutes),
            )
            for task in sorted(open_tasks, key=lambda task: task.due_at)
        ]
    )


async def handle(
    *,
    user_id: str,
    request_id: str,
    audio: bytes | None = None,
    text: str | None = None,
) -> VoiceResponse:
    """요청 하나를 처리해 Pi에 돌려줄 응답을 만든다. 7초 안에 끝나야 한다 (넘으면 status ERROR).

    Pi는 업로드 시작부터 10초를 기다리는데, 이 타이머는 업로드가 끝난 뒤 시작한다.
    업로드·왕복에 쓸 시간을 남겨 Pi가 끊기 전에 ERROR 응답을 받게 한다.

    1. Eureka 미완료 업무 목록 + db의 pending(user_id별 재질문 대기) 조회
    2. ai.analyze() 1회 호출 → Decision (transcript는 응답의 stt_text가 된다)
    3. 정책 검증 — LLM에 맡기지 않고 여기서 강제한다
       - offset_minutes가 없으면 settings.default_reminder_minutes (60)
       - item_id가 1번 목록에 없으면 실행하지 않고 type "clarification"
       - due_at이 과거면 type "clarification"
       - Decision 검증 실패면 status "ERROR" ("다시 한 번 말씀해 주세요")
    4. Eureka 실행 → 2xx와 응답 본문을 확인한 뒤에만 성공 type
    5. output.tts는 템플릿으로 만든다 (LLM을 다시 부르지 않는다). 재질문만 Decision.question을 쓴다
       예: task_created → tts "{name} 업무를 등록했어요. 마감은 {내일 오후 3시}예요.",
           lcd {"expression": "happy", "text": "업무 등록 완료"}
    """
    timings: dict[str, int] = {}
    started = perf_counter()
    status = "ERROR"
    try:
        async with asyncio.timeout(HANDLE_TIMEOUT_SECONDS):
            response = await _handle(
                user_id=user_id,
                request_id=request_id,
                audio=audio,
                text=text,
                timings=timings,
            )
        status = response.status
        return response
    except Exception:
        log.exception("task request failed: request_id=%s", request_id)
        return _error(request_id, "요청을 처리하지 못했어요. 다시 한 번 말씀해 주세요.")
    finally:
        # 음성·받아쓴 문장·토큰은 남기지 않는다. 구간이 없으면 그 앞에서 끝났거나 멈춘 것이다.
        log.info(
            "request_id=%s eureka_list_ms=%s gemini_ms=%s total_ms=%d status=%s",
            request_id,
            timings.get("eureka_list", "-"),
            timings.get("gemini", "-"),
            (perf_counter() - started) * 1000,
            status,
        )


async def _handle(
    *,
    user_id: str,
    request_id: str,
    audio: bytes | None,
    text: str | None,
    timings: dict[str, int],
) -> VoiceResponse:
    now = datetime.now(KST)
    eureka = get_eureka()
    step_started = perf_counter()
    open_tasks = await task_cache.get_open_tasks(eureka)
    timings["eureka_list"] = round((perf_counter() - step_started) * 1000)
    pending = db.get_pending(user_id)
    step_started = perf_counter()
    analyzed = await ai.analyze(
        audio=audio,
        text=text,
        now=now,
        open_tasks=open_tasks[:30],
        pending=pending,
    )
    timings["gemini"] = round((perf_counter() - step_started) * 1000)
    decision = Decision.model_validate(analyzed)

    if decision.action == "create_task":
        name = (decision.args.name or "").strip()
        due_at = decision.args.due_at
        offset = decision.args.offset_minutes
        if not name or due_at is None:
            return _remember_and_clarify(
                user_id, request_id, decision, "업무 이름과 마감 시간을 알려 주세요."
            )
        if due_at.tzinfo is None or due_at.utcoffset() is None or due_at <= now:
            return _remember_and_clarify(
                user_id, request_id, decision, "미래의 마감 시간을 알려 주세요."
            )
        if offset is not None and offset <= 0:
            return _remember_and_clarify(
                user_id, request_id, decision, "알림 시간을 1분 이상으로 알려 주세요."
            )

        created = await _write(eureka, eureka.create_task(name, due_at))
        task_cache.on_created(eureka, created)
        try:
            db.set_reminder(created.item_id, offset or settings.default_reminder_minutes)
        except sqlite3.Error:
            log.exception("task created but reminder was not saved: %s", created.item_id)
            return _error(request_id, f"{created.name} 업무는 등록됐지만 알림 설정을 저장하지 못했어요.")
        _clear_pending_after_success(user_id, pending)
        return VoiceResponse(
            request_id=request_id,
            status="OK",
            stt_text=decision.transcript,
            type="task_created",
            output=Output(
                tts=f"{created.name} 업무를 등록했어요. 마감은 {_format_due(created.due_at)}예요.",
                lcd=Lcd(expression="happy", text="업무 등록 완료"),
            ),
        )

    if decision.action == "list_tasks":
        selected = _filter_tasks(open_tasks, decision.args.range or "all", now)
        if selected:
            shown = selected[:5]
            summary = ", ".join(f"{task.name} {_format_due(task.due_at)}" for task in shown)
            if len(selected) > len(shown):
                summary += f" 외 {len(selected) - len(shown)}개"
            tts = f"미완료 업무 {len(selected)}개가 있어요. {summary}."
        else:
            tts = "해당 기간에 미완료 업무가 없어요."
        _clear_pending_after_success(user_id, pending)
        return VoiceResponse(
            request_id=request_id,
            status="OK",
            stt_text=decision.transcript,
            type="task_list",
            output=Output(tts=tts, lcd=Lcd(expression="speaking", text=f"업무 {len(selected)}건")),
        )

    if decision.action == "complete_task":
        task = _find_task(open_tasks, decision.args.item_id)
        if task is None:
            return _remember_and_clarify(
                user_id, request_id, decision, "어떤 업무를 완료할까요?"
            )
        await _write(eureka, eureka.complete_task(task.item_id))
        task_cache.on_completed(eureka, task.item_id)
        _clear_pending_after_success(user_id, pending)
        return VoiceResponse(
            request_id=request_id,
            status="OK",
            stt_text=decision.transcript,
            type="task_completed",
            output=Output(
                tts=f"{task.name} 업무를 완료했어요.",
                lcd=Lcd(expression="happy", text="업무 완료"),
            ),
        )

    if decision.action == "update_due":
        task = _find_task(open_tasks, decision.args.item_id)
        if task is None:
            return _remember_and_clarify(
                user_id, request_id, decision, "어떤 업무의 마감을 변경할까요?"
            )
        due_at = decision.args.due_at
        if due_at is None:
            return _remember_and_clarify(
                user_id, request_id, decision, f"{task.name} 업무의 마감을 언제로 변경할까요?"
            )
        if due_at.tzinfo is None or due_at.utcoffset() is None or due_at <= now:
            return _remember_and_clarify(
                user_id, request_id, decision, "미래의 마감 시간을 알려 주세요."
            )
        await _write(eureka, eureka.update_due(task.item_id, due_at))
        task_cache.on_due_updated(eureka, task.item_id, due_at)
        _clear_pending_after_success(user_id, pending)
        return VoiceResponse(
            request_id=request_id,
            status="OK",
            stt_text=decision.transcript,
            type="task_updated",
            output=Output(
                tts=f"{task.name} 업무의 마감을 {_format_due(due_at)}로 변경했어요.",
                lcd=Lcd(expression="happy", text="마감 변경 완료"),
            ),
        )

    if decision.action == "clarify":
        return _remember_and_clarify(
            user_id, request_id, decision, decision.question or "어떤 업무인지 더 알려 주세요."
        )

    return _error(request_id, "이 작업은 아직 지원하지 않아요.")


async def _write(eureka, write):
    """Eureka 쓰기를 실행한다. 실패하거나 시간 초과로 끊기면 목록을 바로 다시 조회한다."""
    try:
        return await write
    except (Exception, asyncio.CancelledError):
        task_cache.on_write_failed(eureka)
        raise


def _find_task(tasks: list[Task], item_id: str | None) -> Task | None:
    """AI가 고른 item_id가 미완료 목록에 있을 때만 실행한다 (없는 id·이미 완료된 업무 방지)."""
    return next((task for task in tasks if task.item_id == item_id), None)


def _filter_tasks(tasks: list[Task], range_name: str, now: datetime) -> list[Task]:
    if range_name == "all":
        return tasks
    today = now.astimezone(KST).date()
    if range_name == "today":
        start = end = today
    elif range_name == "tomorrow":
        start = end = today + timedelta(days=1)
    else:  # week: 이번 주 월요일부터 일요일까지
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    return [task for task in tasks if start <= task.due_at.astimezone(KST).date() <= end]


def _format_due(due_at: datetime) -> str:
    local = due_at.astimezone(KST)
    period = "오전" if local.hour < 12 else "오후"
    hour = local.hour % 12 or 12
    minute = f" {local.minute}분" if local.minute else ""
    return f"{local.month}월 {local.day}일 {period} {hour}시{minute}"


def _clarify(request_id: str, transcript: str, question: str) -> VoiceResponse:
    return VoiceResponse(
        request_id=request_id,
        status="OK",
        stt_text=transcript,
        type="clarification",
        output=Output(tts=question, lcd=Lcd(expression="thinking", text="다시 질문")),
    )


def _remember_and_clarify(
    user_id: str, request_id: str, decision: Decision, question: str
) -> VoiceResponse:
    db.set_pending(user_id, decision.model_dump(mode="json"))
    return _clarify(request_id, decision.transcript, question)


def _clear_pending_after_success(user_id: str, pending: dict | None) -> None:
    if pending is None:
        return
    try:
        db.clear_pending(user_id)
    except sqlite3.Error:
        log.exception("request succeeded but pending could not be cleared for user %s", user_id)


def _error(request_id: str, message: str) -> VoiceResponse:
    return VoiceResponse(
        request_id=request_id,
        status="ERROR",
        type="error",
        output=Output(tts=message, lcd=Lcd(expression="thinking", text="처리 실패")),
    )
