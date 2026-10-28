"""음성·텍스트 요청 하나를 처리하는 흐름과 정책 (contracts § 3). 담당: backend.

Eureka는 eureka.py를 통해서만, 저장은 db.py를 통해서만 접근한다.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta

from desk_pet_backend import db
from desk_pet_backend.config import settings
from desk_pet_backend.eureka import KST, get_eureka
from desk_pet_backend.schemas import Decision, Lcd, Output, Task, VoiceResponse
from desk_pet_backend.services import ai

log = logging.getLogger(__name__)


async def handle(
    *,
    user_id: str,
    request_id: str,
    audio: bytes | None = None,
    text: str | None = None,
) -> VoiceResponse:
    """요청 하나를 처리해 Pi에 돌려줄 응답을 만든다. 9초 안에 끝나야 한다 (넘으면 status ERROR).

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
    try:
        async with asyncio.timeout(9):
            return await _handle(user_id=user_id, request_id=request_id, audio=audio, text=text)
    except Exception:
        log.exception("task request failed")
        return _error(request_id, "요청을 처리하지 못했어요. 다시 한 번 말씀해 주세요.")


async def _handle(
    *, user_id: str, request_id: str, audio: bytes | None, text: str | None
) -> VoiceResponse:
    now = datetime.now(KST)
    eureka = get_eureka()
    open_tasks = await eureka.list_open_tasks()
    pending = db.get_pending(user_id)
    decision = Decision.model_validate(await ai.analyze(
        audio=audio,
        text=text,
        now=now,
        open_tasks=open_tasks[:30],
        pending=pending,
    ))

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

        created = await eureka.create_task(name, due_at)
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

    if decision.action == "clarify":
        return _remember_and_clarify(
            user_id, request_id, decision, decision.question or "어떤 업무인지 더 알려 주세요."
        )

    return _error(request_id, "이 작업은 아직 지원하지 않아요.")


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
