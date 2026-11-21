"""음성·텍스트 요청 하나를 처리하는 흐름과 정책 (contracts § 3). 담당: backend.

Eureka는 eureka.py를 통해서만, 저장은 db.py를 통해서만 접근한다.
"""

import asyncio
import logging
import sqlite3
from datetime import date, datetime
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
# 업로드 뒤 읽기·Gemini(재시도 포함)는 15초, Eureka 쓰기는 시작부터 3초.
# Pi는 10초에 지연 안내, 20초에 실패 처리한다 (contracts § 1).
HANDLE_TIMEOUT_SECONDS = 15
WRITE_TIMEOUT_SECONDS = 3

# action이나 필드를 새로 만들 때만 아래 표를 고친다.
TASK_ACTIONS = {"complete_task", "update_due", "delete_task", "set_reminder"}  # item_id가 미완료 목록에 있어야 하는 action
REQUIRED_ARGS = {
    "create_task": ("name", "due_at"),
    "update_due": ("due_at",),
    "set_reminder": ("offset_minutes",),
}

FIELD_LABELS = {"name": "업무 이름", "due_at": "마감 시간", "offset_minutes": "알림 시간"}
CHECK_QUESTIONS = {
    "past_due": "미래의 마감 시간을 알려 주세요.",
    "bad_offset": "알림 시간을 1분 이상으로 알려 주세요.",
}
UNKNOWN_TASK_QUESTIONS = {
    "complete_task": "어떤 업무를 완료할까요?",
    "update_due": "어떤 업무의 마감을 변경할까요?",
    "delete_task": "어떤 업무를 삭제할까요?",
    "set_reminder": "어떤 업무의 알림을 바꿀까요?",
}


async def list_device_tasks() -> DeviceTaskList:
    """Pi 알람에 쓸 미완료 업무를 마감순으로 돌려준다. 알림 간격이 저장되지 않은 업무는 기본값을 쓴다.
    언제 울릴지는 Pi가 판단하므로 마감이 지난 업무도 거르지 않는다.
    알림을 끈 업무도 빼지 않고 reminder_enabled false로 보낸다.
    """
    open_tasks = await task_cache.get_open_tasks(get_eureka())
    reminders = db.get_reminders()
    disabled = db.get_disabled_reminders()
    return DeviceTaskList(
        tasks=[
            DeviceTask(
                item_id=task.item_id,
                name=task.name,
                due_at=task.due_at,
                offset_minutes=reminders.get(task.item_id, settings.default_reminder_minutes),
                reminder_enabled=task.item_id not in disabled,
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
        # 음성·받아쓴 문장·토큰은 남기지 않는다.
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

    def remember_and_clarify(asked: Decision, question: str) -> VoiceResponse:
        return _remember_and_clarify(user_id, request_id, asked, question)

    if decision.action == "unsupported":
        _clear_pending_after_success(user_id, pending)
        return VoiceResponse(
            request_id=request_id,
            status="OK",
            stt_text=decision.transcript,
            type="clarification",
            output=Output(
                tts="업무 등록, 조회, 완료, 마감 변경, 삭제와 업무 알림 설정을 도와드릴 수 있어요.",
                lcd=Lcd(expression="speaking", text="업무 기능 안내"),
            ),
        )

    if delete_item_id is not None:
        # 삭제 확인 질문에 대한 답. 동의 여부는 AI가 confirm으로 판단하고, Backend는 그 답이
        # 확인 질문의 대상 업무에 대한 것인지만 검사한다. 다른 업무·다른 요청이면 삭제를 취소하고 새 요청으로 처리한다.
        db.clear_pending(user_id)
        answered = decision.action == "delete_task" and decision.args.item_id == delete_item_id
        if answered and decision.args.confirm == "yes":
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
        if answered and decision.args.confirm == "no":
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
        # confirm이 null이면 (동의인지 알 수 없음) 아래 delete_task 처리가 같은 업무로 다시 확인한다.

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
        reminder_on = decision.args.reminder_enabled is not False  # 말하지 않으면 켠다

        created = await _write(timer, eureka, eureka.create_task(name, due_at))
        task_cache.on_created(eureka, created)
        try:
            db.set_reminder(created.item_id, offset or settings.default_reminder_minutes, reminder_on)
        except sqlite3.Error:
            log.exception("task created but reminder was not saved: %s", created.item_id)
            return _error(request_id, f"{created.name} 업무는 등록됐지만 알림 설정을 저장하지 못했어요.")
        _clear_pending_after_success(user_id, pending)
        reminder_note = "" if reminder_on else " 알림은 꺼 두었어요."
        return VoiceResponse(
            request_id=request_id,
            status="OK",
            stt_text=decision.transcript,
            type="task_created",
            output=Output(
                tts=(
                    f"{created.name} 업무를 등록했어요. 마감은 {_with_copula(_format_due(created.due_at))}."
                    + reminder_note
                ),
                lcd=Lcd(expression="happy", text="업무 등록 완료"),
            ),
        )

    if decision.action == "list_tasks":
        date_from, date_to = _date_range(decision.args.date_from, decision.args.date_to)
        selected = sorted(
            _filter_tasks(open_tasks, date_from, date_to, now),
            key=lambda task: task.due_at,
        )
        # AI가 이해한 기간을 읽어 줘서, 잘못 이해했으면 사용자가 바로 알 수 있게 한다.
        period = _format_period(date_from, date_to)
        prefix = f"{period} " if period else ""
        if selected:
            shown = selected[:5]
            summary = ", ".join(f"{task.name} {_format_due(task.due_at)}" for task in shown)
            if len(selected) > len(shown):
                summary += f" 외 {len(selected) - len(shown)}개"
            tts = f"{prefix}미완료 업무 {len(selected)}개가 있어요. {summary}."
        else:
            tts = f"{prefix}미완료 업무가 없어요."
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

    if decision.action == "set_reminder":
        # 알림 간격·켜짐 여부만 바꾼다. Eureka에는 없는 값이라 쓰지 않고, Pi는 GET /api/tasks로 새 값을 받는다.
        # reminder_enabled false → 끈다 (간격은 남긴다)
        # reminder_enabled true, 간격 없음 → 켜고 저장된 간격을 쓴다
        # 간격 있음 → 간격을 저장하고 켠다
        task = _find_task(open_tasks, decision.args.item_id)
        offset = decision.args.offset_minutes
        enabled = decision.args.reminder_enabled
        try:
            if enabled is False:
                db.set_reminder_enabled(task.item_id, False, settings.default_reminder_minutes)
            elif offset is None:
                offset = db.set_reminder_enabled(task.item_id, True, settings.default_reminder_minutes)
            else:
                db.set_reminder(task.item_id, offset)
        except sqlite3.Error:
            log.exception("reminder was not saved: %s", task.item_id)
            return _error(request_id, f"{task.name} 업무의 알림 설정을 저장하지 못했어요.")
        _clear_pending_after_success(user_id, pending)
        if enabled is False:
            output = Output(tts=f"{task.name} 업무 알림을 껐어요.", lcd=Lcd(expression="happy", text="알림 끔"))
        elif decision.args.offset_minutes is None:
            output = Output(
                tts=f"{task.name} 업무 알림을 다시 켰어요. 마감 {_format_offset(offset)} 전에 알려 드려요.",
                lcd=Lcd(expression="happy", text="알림 켬"),
            )
        else:
            output = Output(
                tts=f"{task.name} 업무 알림을 마감 {_format_offset(offset)} 전으로 바꿨어요.",
                lcd=Lcd(expression="happy", text="알림 설정 완료"),
            )
        return VoiceResponse(
            request_id=request_id,
            status="OK",
            stt_text=decision.transcript,
            type="reminder_set",
            output=output,
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
    required = REQUIRED_ARGS.get(decision.action, ())
    if decision.action == "set_reminder" and args.reminder_enabled is not None:
        required = ()  # 켜기·끄기만 하면 알림 간격은 없어도 된다
    missing = tuple(
        field
        for field in required
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


def _date_range(date_from: date | None, date_to: date | None) -> tuple[date | None, date | None]:
    """AI가 앞뒤를 바꿔 내면 순서만 바로잡는다. 날짜의 뜻은 해석하지 않는다."""
    if date_from is not None and date_to is not None and date_from > date_to:
        return date_to, date_from
    return date_from, date_to


def _filter_tasks(
    tasks: list[Task], date_from: date | None, date_to: date | None, now: datetime
) -> list[Task]:
    """마감 날짜(KST)가 date_from~date_to(양 끝 포함)인 업무를 고른다. 없는 쪽은 열려 있다.
    마감이 지난 업무는 어느 범위에서도 읽지 않는다.
    """
    def due_date(task: Task) -> date:
        return task.due_at.astimezone(KST).date()

    return [
        task
        for task in tasks
        if task.due_at >= now
        and (date_from is None or due_date(task) >= date_from)
        and (date_to is None or due_date(task) <= date_to)
    ]


def _format_period(date_from: date | None, date_to: date | None) -> str:
    """조회 기간을 읽기 좋게. 같은 날 → "11월 21일", 범위 → "11월 19일부터 11월 22일까지", 없으면 ""."""
    def day(value: date) -> str:
        return f"{value.month}월 {value.day}일"

    if date_from is not None and date_from == date_to:
        return day(date_from)
    parts = []
    if date_from is not None:
        parts.append(f"{day(date_from)}부터")
    if date_to is not None:
        parts.append(f"{day(date_to)}까지")
    return " ".join(parts)


def _format_due(due_at: datetime) -> str:
    local = due_at.astimezone(KST)
    period = "오전" if local.hour < 12 else "오후"
    hour = local.hour % 12 or 12
    minute = f" {local.minute}분" if local.minute else ""
    return f"{local.month}월 {local.day}일 {period} {hour}시{minute}"


def _format_offset(minutes: int) -> str:
    """알림 간격(분)을 읽기 좋게 바꾼다. 30 → "30분", 90 → "1시간 30분", 1440 → "1일"."""
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [f"{days}일"] * bool(days) + [f"{hours}시간"] * bool(hours) + [f"{mins}분"] * bool(mins)
    return " ".join(parts)


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
