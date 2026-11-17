"""음성·텍스트 요청 하나를 처리하는 흐름과 정책 (contracts § 3). 담당: backend.

Eureka는 eureka.py를 통해서만, 저장은 db.py를 통해서만 접근한다.
"""

import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from time import perf_counter
from typing import NamedTuple

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
# 11/06 결정: 업로드 뒤 읽기·Gemini(재시도 포함)는 15초, Eureka 쓰기는 시작부터 3초.
# Pi는 10초에 지연 안내, 20초에 실패 처리한다 (contracts § 1).
HANDLE_TIMEOUT_SECONDS = 15
WRITE_TIMEOUT_SECONDS = 3

# 실행 전제조건 (check_decision). Gemini 오류를 볼 때마다 검사를 늘리지 않는다 — 오류는 프롬프트와 평가셋으로 고친다.
# action이나 필드를 새로 만들 때만 아래 표를 고친다.
TASK_ACTIONS = {"complete_task", "update_due", "delete_task"}  # item_id가 미완료 목록에 있어야 하는 action
REQUIRED_ARGS = {
    "create_task": ("name", "due_at"),
    "update_due": ("due_at",),
}
# 빠진 값은 이 이름표를 이어 붙여 한 번에 묻는다 ("업무 이름과 마감 시간을 알려 주세요.").
FIELD_LABELS = {"name": "업무 이름", "due_at": "마감 시간"}
CHECK_QUESTIONS = {
    "past_due": "미래의 마감 시간을 알려 주세요.",
    "bad_offset": "알림 시간을 1분 이상으로 알려 주세요.",
}
UNKNOWN_TASK_QUESTIONS = {
    "complete_task": "어떤 업무를 완료할까요?",
    "update_due": "어떤 업무의 마감을 변경할까요?",
    "delete_task": "어떤 업무를 삭제할까요?",
}
# 삭제는 되돌릴 수 없을 수 있으므로 문장 전체가 정확히 일치할 때만 답으로 본다.
DELETE_YES = {
    "응", "응응", "네", "네네", "예", "예예", "그래", "그래요", "맞아", "맞아요", "맞습니다",
    "응맞아", "네맞아요", "예맞아요", "좋아", "좋아요", "네좋아요", "해줘", "해주세요",
    "응해줘", "네해주세요", "그래해줘", "삭제", "삭제해", "삭제해줘", "삭제해주세요", "삭제할게",
    "응삭제", "네삭제", "응삭제해", "응삭제해줘", "네삭제해", "네삭제해줘", "네삭제해주세요",
    "예삭제해주세요", "그래삭제해", "그래삭제해줘", "지워", "지워줘", "지워주세요", "응지워",
    "응지워줘", "네지워주세요", "그래지워", "그래지워줘",
}
DELETE_NO = {
    "아니", "아니야", "아니요", "아니오", "아뇨", "아니에요", "취소", "취소해", "취소해줘",
    "취소해주세요", "취소할게", "취소할게요", "아니취소", "아니취소해", "아니요취소해주세요",
    "하지마", "하지마세요", "삭제하지마", "삭제하지마세요", "아니삭제하지마", "지우지마",
    "지우지마세요", "삭제안해", "안해", "안할래", "안할게요", "그만", "그만해", "됐어", "됐어요",
}


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
    """요청 하나를 처리해 Pi에 돌려줄 응답을 만든다. 넘으면 status ERROR.

    제한 시간: Eureka 쓰기 전까지 HANDLE_TIMEOUT_SECONDS(15초), 쓰기는 시작부터 WRITE_TIMEOUT_SECONDS(3초).
    타이머는 업로드가 끝난 뒤 시작한다. Pi는 20초에 실패 처리하므로 업로드·왕복에 쓸 시간을 남긴다.

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
        async with asyncio.timeout(HANDLE_TIMEOUT_SECONDS) as timer:
            response = await _handle(
                timer=timer,
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
    timer: asyncio.Timeout,
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
    pending, delete_item_id = _split_pending(db.get_pending(user_id))
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
    spoken = text if text is not None else decision.transcript

    def remember_and_clarify(asked: Decision, question: str) -> VoiceResponse:
        return _remember_and_clarify(user_id, request_id, asked, question)

    if delete_item_id is not None:
        # 삭제 대기 중인 답은 AI 판단과 상관없이 받아쓴 문장으로 판정한다. 그 밖의 말은 삭제를 취소한다.
        db.clear_pending(user_id)
        answer = _compact_answer(spoken)
        if answer in DELETE_YES:
            task = _find_task(open_tasks, delete_item_id)
            if task is None:
                return _error(request_id, "삭제할 업무를 찾지 못했어요.")
            await _write(timer, eureka, eureka.delete_task(task.item_id))
            task_cache.on_deleted(eureka, task.item_id)
            log.info("task deleted: request_id=%s item_id=%s name=%s", request_id, task.item_id, task.name)
            try:
                db.delete_reminder(task.item_id)
            except sqlite3.Error:
                log.exception("task deleted but reminder was not removed: %s", task.item_id)
            return VoiceResponse(
                request_id=request_id,
                status="OK",
                stt_text=decision.transcript,
                type="task_deleted",
                output=Output(
                    tts=f"{task.name} 업무를 삭제했어요.",
                    lcd=Lcd(expression="happy", text="업무 삭제 완료"),
                ),
            )
        if answer in DELETE_NO:
            return VoiceResponse(
                request_id=request_id,
                status="OK",
                stt_text=decision.transcript,
                type="task_delete_cancelled",
                output=Output(
                    tts="삭제하지 않았어요.",
                    lcd=Lcd(expression="speaking", text="삭제 취소"),
                ),
            )

    if decision.action in REQUIRED_ARGS or decision.action in TASK_ACTIONS:
        blocked = check_decision(decision, open_tasks, now)
        if blocked is not None:
            return remember_and_clarify(decision, _check_question(decision.action, blocked))

    if decision.action == "delete_task":
        task = _find_task(open_tasks, decision.args.item_id)
        db.set_pending(
            user_id,
            {
                **decision.model_dump(mode="json"),
                "delete_item_id": task.item_id,
            },
        )
        return _clarify(
            request_id,
            decision.transcript,
            f"{task.name} 업무를 삭제할까요? 마감은 {_with_copula(_format_due(task.due_at))}.",
        )

    if decision.action == "create_task":
        name = decision.args.name.strip()
        due_at = decision.args.due_at
        offset = decision.args.offset_minutes

        created = await _write(timer, eureka, eureka.create_task(name, due_at))
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
                tts=f"{created.name} 업무를 등록했어요. 마감은 {_with_copula(_format_due(created.due_at))}.",
                lcd=Lcd(expression="happy", text="업무 등록 완료"),
            ),
        )

    if decision.action == "list_tasks":
        selected = sorted(
            _filter_tasks(open_tasks, decision.args.range or "all", now),
            key=lambda task: task.due_at,
        )
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
        await _write(timer, eureka, eureka.complete_task(task.item_id))
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
        due_at = decision.args.due_at
        await _write(timer, eureka, eureka.update_due(task.item_id, due_at))
        task_cache.on_due_updated(eureka, task.item_id, due_at)
        _clear_pending_after_success(user_id, pending)
        return VoiceResponse(
            request_id=request_id,
            status="OK",
            stt_text=decision.transcript,
            type="task_updated",
            output=Output(
                tts=f"{task.name} 업무의 마감을 {_with_direction(_format_due(due_at))} 변경했어요.",
                lcd=Lcd(expression="happy", text="마감 변경 완료"),
            ),
        )

    if decision.action == "clarify":
        return remember_and_clarify(decision, decision.question or "어떤 업무인지 더 알려 주세요.")

    return _error(request_id, "이 작업은 아직 지원하지 않아요.")


async def _write(timer: asyncio.Timeout, eureka, write):
    """Eureka 쓰기를 실행한다. 실패하거나 시간 초과로 끊기면 목록을 바로 다시 조회한다.

    쓰기를 시작하면 남은 시간과 상관없이 마감을 지금부터 WRITE_TIMEOUT_SECONDS로 다시 잡는다.
    """
    timer.reschedule(asyncio.get_running_loop().time() + WRITE_TIMEOUT_SECONDS)
    try:
        return await write
    except (Exception, asyncio.CancelledError):
        task_cache.on_write_failed(eureka)
        raise


class Blocked(NamedTuple):
    """check_decision이 실행을 막은 이유. reason이 "missing"이면 fields에 빠진 값이 모두 들어 있다."""

    reason: str  # unknown_task | missing | past_due | bad_offset
    fields: tuple[str, ...] = ()


def check_decision(decision: Decision, open_tasks: list[Task], now: datetime) -> Blocked | None:
    """실행 전제조건만 검사한다. 통과하면 None, 막히면 Blocked.

    말뜻을 해석하지 않고 값만 본다. 무엇을 할지 판단하는 것은 AI다.
    """
    args = decision.args
    if decision.action in TASK_ACTIONS and _find_task(open_tasks, args.item_id) is None:
        return Blocked("unknown_task")
    missing = tuple(
        field
        for field in REQUIRED_ARGS.get(decision.action, ())
        if getattr(args, field) is None
        or (isinstance(getattr(args, field), str) and not getattr(args, field).strip())
    )
    if missing:
        return Blocked("missing", missing)
    if args.due_at is not None and (args.due_at.utcoffset() is None or args.due_at <= now):
        return Blocked("past_due")
    if args.offset_minutes is not None and args.offset_minutes <= 0:
        return Blocked("bad_offset")
    return None


def _check_question(action: str, blocked: Blocked) -> str:
    if blocked.reason == "unknown_task":
        return UNKNOWN_TASK_QUESTIONS[action]
    if blocked.reason == "missing":
        return f"{_join_with_and([FIELD_LABELS[field] for field in blocked.fields])} 알려 주세요."
    return CHECK_QUESTIONS[blocked.reason]


def _join_with_and(labels: list[str]) -> str:
    """["업무 이름", "마감 시간"] → "업무 이름과 마감 시간을". 조사는 받침에 맞춘다."""
    text = labels[0]
    for label in labels[1:]:
        text += ("과 " if _final_consonant(text) else "와 ") + label
    return text + ("을" if _final_consonant(text) else "를")


def _compact_answer(spoken: str) -> str:
    return re.sub(r"[\s.,!?~…]", "", spoken)


def _split_pending(stored: dict | None) -> tuple[dict | None, str | None]:
    """저장된 재질문 맥락을 AI에 넘길 Decision과 삭제 대기 대상으로 나눈다."""
    if stored is None:
        return None, None
    pending = dict(stored)
    pending.pop("utterances", None)  # 이름 검사를 없애기 전에 저장된 값 (60초 뒤 사라진다)
    delete_item_id = pending.pop("delete_item_id", None)
    return pending, delete_item_id


def _find_task(tasks: list[Task], item_id: str | None) -> Task | None:
    """AI가 고른 item_id가 미완료 목록에 있을 때만 실행한다 (없는 id·이미 완료된 업무 방지)."""
    return next((task for task in tasks if task.item_id == item_id), None)


def _filter_tasks(tasks: list[Task], range_name: str, now: datetime) -> list[Task]:
    """요청 범위의 업무를 고른다. 마감이 지난 업무는 어느 범위에서도 읽지 않는다."""
    tasks = [task for task in tasks if task.due_at >= now]
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


def _final_consonant(text: str) -> int:
    """마지막 글자의 받침 번호를 돌려준다. 받침이 없거나 한글이 아니면 0, ㄹ은 8."""
    last = text[-1]
    return (ord(last) - 0xAC00) % 28 if "가" <= last <= "힣" else 0


def _with_copula(text: str) -> str:
    return f"{text}이에요" if _final_consonant(text) else f"{text}예요"


def _with_direction(text: str) -> str:
    return f"{text}로" if _final_consonant(text) in (0, 8) else f"{text}으로"


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
