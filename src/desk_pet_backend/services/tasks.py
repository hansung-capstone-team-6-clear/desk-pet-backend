"""음성·텍스트 요청 하나를 처리하는 흐름과 정책 (contracts § 3). 담당: backend.

Eureka는 eureka.py를 통해서만, 저장은 db.py를 통해서만 접근한다.
"""

import asyncio
import logging
import re
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
# 11/06 결정: 업로드 뒤 읽기·Gemini(재시도 포함)는 15초, Eureka 쓰기는 시작부터 3초.
# Pi는 10초에 지연 안내, 20초에 실패 처리한다 (contracts § 1).
HANDLE_TIMEOUT_SECONDS = 15
WRITE_TIMEOUT_SECONDS = 3
CREATE_FLOW_ACTIONS = {"create_task", "clarify"}
# 날짜·시간 표현과 일반어. 이것만으로 된 이름은 업무 이름이 아니다 (공백을 뺀 문장에 적용).
NAME_NOISE = re.compile(
    r"오늘|내일|모레|글피|어제|그제|이번주|다음주|지난주|이번달|다음달|지난달|올해|내년"
    r"|[월화수목금토일]요일|\d+(?:월|일|시|분|주)?|(?:열한|열두|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)시"
    r"|오전|오후|아침|점심|저녁|밤|새벽|정오|자정|반|까지|부터|전|쯤|마감|기한"
    r"|해야할일|할일|업무|일정|작업|태스크|task|todo|등록|추가|하나|새로운|새|것|거|일|\W|_"
)
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
    pending, previous_utterances, delete_item_id = _split_pending(db.get_pending(user_id))
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
    utterances = _flow_utterances(pending, previous_utterances, decision.action, spoken)

    def remember_and_clarify(asked: Decision, question: str) -> VoiceResponse:
        return _remember_and_clarify(user_id, request_id, asked, utterances, question)

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

    if decision.action == "delete_task":
        task = _find_task(open_tasks, decision.args.item_id)
        if task is None:
            return remember_and_clarify(decision, "어떤 업무를 삭제할까요?")
        db.set_pending(
            user_id,
            {
                **decision.model_dump(mode="json"),
                "utterances": utterances,
                "delete_item_id": task.item_id,
            },
        )
        return _clarify(
            request_id,
            decision.transcript,
            f"{task.name} 업무를 삭제할까요? 마감은 {_with_copula(_format_due(task.due_at))}.",
        )

    if decision.action == "create_task":
        name = (decision.args.name or "").strip()
        due_at = decision.args.due_at
        offset = decision.args.offset_minutes
        if name and (is_generic_name(name) or name_source(name, utterances, open_tasks) is None):
            # 근거 없는 이름만 지운다. 이 요청의 마감·알림 값은 다시 묻지 않도록 남긴다.
            without_name = decision.args.model_copy(update={"name": None})
            return remember_and_clarify(
                decision.model_copy(update={"args": without_name}), "어떤 일을 추가할까요?"
            )
        if not name or due_at is None:
            return remember_and_clarify(decision, "업무 이름과 마감 시간을 알려 주세요.")
        if due_at.tzinfo is None or due_at.utcoffset() is None or due_at <= now:
            return remember_and_clarify(decision, "미래의 마감 시간을 알려 주세요.")
        if offset is not None and offset <= 0:
            return remember_and_clarify(decision, "알림 시간을 1분 이상으로 알려 주세요.")

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
        if task is None:
            return remember_and_clarify(decision, "어떤 업무를 완료할까요?")
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
        if task is None:
            return remember_and_clarify(decision, "어떤 업무의 마감을 변경할까요?")
        due_at = decision.args.due_at
        if due_at is None:
            return remember_and_clarify(decision, f"{task.name} 업무의 마감을 언제로 변경할까요?")
        if due_at.tzinfo is None or due_at.utcoffset() is None or due_at <= now:
            return remember_and_clarify(decision, "미래의 마감 시간을 알려 주세요.")
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


def _compact(value: str) -> str:
    return "".join(value.split()).casefold()


def is_generic_name(name: str) -> bool:
    """날짜·시간 표현과 일반어를 빼면 아무것도 남지 않는 이름인지 본다 ("할 일", "내일 오후 3시")."""
    return not NAME_NOISE.sub("", _compact(name))


def name_source(name: str, utterances: list[str], open_tasks: list[Task]) -> str | None:
    """새 업무 이름의 근거를 사용자 발화에서 찾는다. 업무 목록은 근거로 쓰지 않는다.

    "exact": 공백을 뺀 이름이 한 발화에 연속으로 나온다.
    "words": 목록에 없는 이름의 단어가 한 발화에 모두 나온다 (보조 필터).
    None: 근거가 없다.

    오판을 줄이는 장치일 뿐 등록 의도를 증명하지는 않는다. 단어를 조합한 이름("보고서랑 발표 준비해" →
    "발표 보고서 준비")이나, 수정 요청을 등록으로 잘못 판단한 경우는 막지 못한다.
    """
    spoken = [_compact(utterance) for utterance in utterances]
    compact_name = _compact(name)
    if any(compact_name in utterance for utterance in spoken):
        return "exact"
    if any(_compact(task.name) == compact_name for task in open_tasks):
        return None
    words = [word.casefold() for word in name.split()]
    if any(all(word in utterance for word in words) for utterance in spoken):
        return "words"
    return None


def _compact_answer(spoken: str) -> str:
    return re.sub(r"[\s.,!?~…]", "", spoken)


def _split_pending(stored: dict | None) -> tuple[dict | None, list[str], str | None]:
    """저장된 재질문 맥락을 AI에 넘길 Decision, 이름 검증용 발화, 삭제 대기 대상으로 나눈다."""
    if stored is None:
        return None, [], None
    pending = dict(stored)
    utterances = pending.pop("utterances", None)
    delete_item_id = pending.pop("delete_item_id", None)
    if utterances is None:
        transcript = pending.get("transcript")
        utterances = [transcript] if transcript else []
    return pending, utterances, delete_item_id


def _flow_utterances(
    pending: dict | None, previous: list[str], action: str, utterance: str
) -> list[str]:
    """이번 등록 흐름의 발화를 최초 요청 1개 + 최근 2개로 유지한다. 완료·변경 재질문의 발화는 잇지 않는다."""
    continues = (
        pending is not None
        and pending.get("action") in CREATE_FLOW_ACTIONS
        and action in CREATE_FLOW_ACTIONS
    )
    history = [*previous, utterance] if continues else [utterance]
    return history[:1] + history[1:][-2:]


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
    user_id: str, request_id: str, decision: Decision, utterances: list[str], question: str
) -> VoiceResponse:
    db.set_pending(user_id, {**decision.model_dump(mode="json"), "utterances": utterances})
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
