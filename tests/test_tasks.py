"""AI 판단 뒤 등록·조회 정책과 응답을 검증한다. 실제 Eureka는 호출하지 않는다."""

import sqlite3
from datetime import UTC, date, datetime, time, timedelta

import pytest

from desk_pet_backend import db
from desk_pet_backend.config import settings
from desk_pet_backend.eureka import KST, EurekaError, EurekaMock
from desk_pet_backend.schemas import Decision, DecisionArgs, Task
from desk_pet_backend.services import tasks


@pytest.fixture
def mock_flow(monkeypatch, tmp_path):
    eureka = EurekaMock()
    monkeypatch.setattr(tasks, "get_eureka", lambda: eureka)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "tasks.db"))
    return eureka


def set_decision(monkeypatch, decision):
    async def analyze(**kwargs):
        return decision

    monkeypatch.setattr(tasks.ai, "analyze", analyze)


async def test_create_saves_reminder_and_returns_success_only_after_eureka(mock_flow, monkeypatch):
    due = datetime.now(KST) + timedelta(days=1)
    set_decision(monkeypatch, Decision(
        transcript="내일 보고서 등록", action="create_task",
        args=DecisionArgs(name="보고서", due_at=due, offset_minutes=30),
    ))

    response = await tasks.handle(user_id="pet-01", request_id="req-1", text="내일 보고서 등록")

    assert response.status == "OK"
    assert response.type == "task_created"
    assert response.request_id == "req-1"
    assert response.stt_text == "내일 보고서 등록"
    assert "보고서 업무를 등록했어요" in response.output.tts
    created = (await mock_flow.list_open_tasks())[0]
    with db.connect() as conn:
        row = conn.execute("SELECT offset_min FROM reminders WHERE item_id = ?", (created.item_id,)).fetchone()
    assert row["offset_min"] == 30


async def test_create_uses_default_reminder_when_unspecified(mock_flow, monkeypatch):
    monkeypatch.setattr(settings, "default_reminder_minutes", 60)
    set_decision(monkeypatch, Decision(
        transcript="업무 등록", action="create_task",
        args=DecisionArgs(name="회의", due_at=datetime.now(KST) + timedelta(days=1)),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-2", text="회의 업무 등록")
    assert response.type == "task_created"
    created = (await mock_flow.list_open_tasks())[0]
    with db.connect() as conn:
        row = conn.execute("SELECT offset_min FROM reminders WHERE item_id = ?", (created.item_id,)).fetchone()
    assert row["offset_min"] == 60


async def test_past_due_clarifies_without_creating_task(mock_flow, monkeypatch):
    set_decision(monkeypatch, Decision(
        transcript="어제 업무 등록", action="create_task",
        args=DecisionArgs(name="보고서", due_at=datetime.now(KST) - timedelta(days=1)),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-3", text="어제 보고서 등록")
    assert response.type == "clarification"
    assert response.status == "OK"
    assert await mock_flow.list_open_tasks() == []


def freeze_now(monkeypatch, now):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(tasks, "datetime", FixedDatetime)


async def test_list_filters_by_kst_date_and_hides_done(mock_flow, monkeypatch):
    today = datetime.now(KST).date()
    freeze_now(monkeypatch, datetime.combine(today, time(12), KST))
    today_task = await mock_flow.create_task("오늘 업무", datetime.combine(today, time(15), KST))
    await mock_flow.create_task("내일 업무", datetime.combine(today + timedelta(days=1), time(15), KST))
    done = await mock_flow.create_task("종료된 보고서", datetime.combine(today, time(16), KST))
    await mock_flow.complete_task(done.item_id)
    set_decision(monkeypatch, Decision(
        transcript="오늘 할 일", action="list_tasks", args=DecisionArgs(date_from=today, date_to=today),
    ))

    response = await tasks.handle(user_id="pet-01", request_id="req-4", text="오늘 할 일")

    assert response.status == "OK"
    assert response.type == "task_list"
    assert response.output.tts.startswith(f"{today.month}월 {today.day}일 미완료 업무 1개가 있어요.")
    assert "오늘 업무" in response.output.tts
    assert "내일 업무" not in response.output.tts
    assert "종료된 보고서" not in response.output.tts
    assert response.output.lcd.text == "업무 1건"
    assert today_task.item_id in mock_flow.tasks


@pytest.mark.parametrize("date_from, date_to, expected", [
    (date(2026, 10, 28), date(2026, 10, 28), ["today"]),
    (date(2026, 10, 29), date(2026, 10, 29), ["tomorrow"]),
    (date(2026, 10, 28), date(2026, 11, 1), ["today", "tomorrow"]),
    (date(2026, 11, 2), date(2026, 11, 8), ["next_week"]),
    (None, date(2026, 10, 29), ["today", "tomorrow"]),
    (date(2026, 10, 29), None, ["tomorrow", "next_week"]),
    (None, None, ["today", "tomorrow", "next_week"]),
])
def test_filter_uses_inclusive_kst_dates(date_from, date_to, expected):
    now = datetime(2026, 10, 28, 12, tzinfo=KST)  # 수요일
    items = [
        Task(item_id="1", name="today", due_at=datetime(2026, 10, 28, 15, tzinfo=KST), status="todo"),
        # UTC로는 10/29 14:59지만 KST로는 10/29 23:59 → 내일
        Task(item_id="2", name="tomorrow", due_at=datetime(2026, 10, 29, 14, 59, tzinfo=UTC), status="todo"),
        Task(item_id="3", name="next_week", due_at=datetime(2026, 11, 2, 15, tzinfo=KST), status="todo"),
    ]
    assert [task.name for task in tasks._filter_tasks(items, date_from, date_to, now)] == expected


def test_reversed_date_range_is_swapped():
    assert tasks._date_range(date(2026, 11, 22), date(2026, 11, 19)) == (date(2026, 11, 19), date(2026, 11, 22))
    assert tasks._date_range(None, date(2026, 11, 19)) == (None, date(2026, 11, 19))


@pytest.mark.parametrize("date_from, date_to, period", [
    (date(2026, 11, 21), date(2026, 11, 21), "11월 21일"),
    (date(2026, 11, 19), date(2026, 11, 22), "11월 19일부터 11월 22일까지"),
    (None, date(2026, 11, 20), "11월 20일까지"),
    (date(2026, 11, 23), None, "11월 23일부터"),
    (None, None, ""),
])
def test_format_period(date_from, date_to, period):
    assert tasks._format_period(date_from, date_to) == period


async def test_empty_list_reads_period(mock_flow, monkeypatch):
    set_decision(monkeypatch, Decision(
        transcript="모레 뭐 있어", action="list_tasks",
        args=DecisionArgs(date_from=date(2099, 1, 2), date_to=date(2099, 1, 2)),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-70", text="모레 뭐 있어")
    assert response.output.tts == "1월 2일 미완료 업무가 없어요."


async def test_eureka_failure_does_not_claim_create_success(mock_flow, monkeypatch):
    set_decision(monkeypatch, Decision(
        transcript="업무 등록", action="create_task",
        args=DecisionArgs(name="보고서", due_at=datetime.now(KST) + timedelta(days=1)),
    ))

    async def fail_create(name, due_at):
        raise EurekaError("test failure")

    monkeypatch.setattr(mock_flow, "create_task", fail_create)
    response = await tasks.handle(user_id="pet-01", request_id="req-5", text="보고서 등록")
    assert response.status == "ERROR"
    assert response.type == "error"
    assert "등록했어요" not in response.output.tts
    assert mock_flow.tasks == {}


async def test_ai_failure_returns_error_without_creating_task(mock_flow, monkeypatch):
    async def fail_analyze(**kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(tasks.ai, "analyze", fail_analyze)
    response = await tasks.handle(user_id="pet-01", request_id="req-6", text="업무 등록")
    assert response.status == "ERROR"
    assert response.type == "error"
    assert mock_flow.tasks == {}


async def test_clarification_is_passed_to_next_request_and_cleared_after_create(
    mock_flow, monkeypatch
):
    first = Decision(
        transcript="보고서 등록해줘", action="clarify", args=DecisionArgs(),
        question="마감은 언제인가요?",
    )
    due = datetime.combine(datetime.now(KST).date() + timedelta(days=1), time(15), KST)
    followup = Decision(
        transcript="내일 오후 3시", action="create_task",
        args=DecisionArgs(name="보고서", due_at=due),
    )
    calls = []

    async def analyze(**kwargs):
        calls.append(kwargs)
        return first if len(calls) == 1 else followup

    monkeypatch.setattr(tasks.ai, "analyze", analyze)
    response = await tasks.handle(user_id="pet-01", request_id="req-7", text="보고서 등록해줘")
    assert response.type == "clarification"
    assert response.output.tts == "마감은 언제인가요?"
    assert calls[0]["pending"] is None
    assert db.get_pending("pet-01") == first.model_dump(mode="json")
    assert db.get_pending("pet-02") is None
    assert mock_flow.tasks == {}

    response = await tasks.handle(user_id="pet-01", request_id="req-8", text="내일 오후 3시")
    assert calls[1]["pending"] == first.model_dump(mode="json")
    assert response.type == "task_created"
    assert db.get_pending("pet-01") is None
    assert len(mock_flow.tasks) == 1


def test_expired_pending_is_removed(mock_flow):
    db.set_pending("pet-01", {"transcript": "지난 요청"})
    with db.connect() as conn:
        conn.execute("UPDATE pending SET expires_at = 0 WHERE user_id = ?", ("pet-01",))

    assert db.get_pending("pet-01") is None
    with db.connect() as conn:
        row = conn.execute("SELECT 1 FROM pending WHERE user_id = ?", ("pet-01",)).fetchone()
    assert row is None


async def test_complete_marks_task_done_and_returns_success(mock_flow, monkeypatch):
    created = await mock_flow.create_task("보고서", datetime.now(KST) + timedelta(days=1))
    set_decision(monkeypatch, Decision(
        transcript="보고서 완료했어", action="complete_task",
        args=DecisionArgs(item_id=created.item_id),
    ))

    response = await tasks.handle(user_id="pet-01", request_id="req-9", text="보고서 완료했어")

    assert response.status == "OK"
    assert response.type == "task_completed"
    assert response.output.tts == "보고서 업무를 완료했어요."
    assert response.output.lcd.expression == "happy"
    assert mock_flow.tasks[created.item_id].status == "done"


@pytest.mark.parametrize("item_id", [None, "unknown"])
async def test_complete_unknown_task_clarifies_without_calling_eureka(mock_flow, monkeypatch, item_id):
    set_decision(monkeypatch, Decision(
        transcript="그거 완료했어", action="complete_task", args=DecisionArgs(item_id=item_id),
    ))

    async def fail_complete(item_id):
        raise AssertionError("Eureka must not be called")

    monkeypatch.setattr(mock_flow, "complete_task", fail_complete)
    response = await tasks.handle(user_id="pet-01", request_id="req-10", text="그거 완료했어")
    assert response.type == "clarification"
    assert response.output.tts == "어떤 업무를 완료할까요?"
    assert db.get_pending("pet-01")["action"] == "complete_task"


async def test_eureka_failure_does_not_claim_complete_success(mock_flow, monkeypatch):
    created = await mock_flow.create_task("보고서", datetime.now(KST) + timedelta(days=1))
    set_decision(monkeypatch, Decision(
        transcript="보고서 완료했어", action="complete_task",
        args=DecisionArgs(item_id=created.item_id),
    ))

    async def fail_complete(item_id):
        raise EurekaError("test failure")

    monkeypatch.setattr(mock_flow, "complete_task", fail_complete)
    response = await tasks.handle(user_id="pet-01", request_id="req-11", text="보고서 완료했어")
    assert response.status == "ERROR"
    assert "완료했어요" not in response.output.tts
    assert mock_flow.tasks[created.item_id].status == "todo"


async def test_update_due_changes_task_and_returns_success(mock_flow, monkeypatch):
    created = await mock_flow.create_task("보고서", datetime.now(KST) + timedelta(days=1))
    new_due = datetime.combine(datetime.now(KST).date() + timedelta(days=3), time(15), KST)
    set_decision(monkeypatch, Decision(
        transcript="보고서 마감 미뤄줘", action="update_due",
        args=DecisionArgs(item_id=created.item_id, due_at=new_due),
    ))

    response = await tasks.handle(user_id="pet-01", request_id="req-12", text="보고서 마감 미뤄줘")

    assert response.status == "OK"
    assert response.type == "task_updated"
    assert "보고서 업무의 마감을" in response.output.tts
    assert "오후 3시로 변경했어요" in response.output.tts
    assert response.output.lcd.expression == "happy"
    assert mock_flow.tasks[created.item_id].due_at == new_due


@pytest.mark.parametrize("known_item, due_delta, question", [
    (False, timedelta(days=1), "어떤 업무의 마감을 변경할까요?"),
    (True, None, "마감 시간을 알려 주세요."),
    (True, timedelta(days=-1), "미래의 마감 시간을 알려 주세요."),
])
async def test_update_due_clarifies_without_calling_eureka(
    mock_flow, monkeypatch, known_item, due_delta, question
):
    created = await mock_flow.create_task("보고서", datetime.now(KST) + timedelta(days=1))
    due_at = None if due_delta is None else datetime.now(KST) + due_delta
    set_decision(monkeypatch, Decision(
        transcript="마감 바꿔줘", action="update_due",
        args=DecisionArgs(item_id=created.item_id if known_item else "unknown", due_at=due_at),
    ))

    async def fail_update(item_id, due_at):
        raise AssertionError("Eureka must not be called")

    monkeypatch.setattr(mock_flow, "update_due", fail_update)
    response = await tasks.handle(user_id="pet-01", request_id="req-13", text="마감 바꿔줘")
    assert response.type == "clarification"
    assert response.output.tts == question
    assert db.get_pending("pet-01")["action"] == "update_due"


async def test_eureka_failure_does_not_claim_update_success(mock_flow, monkeypatch):
    original_due = datetime.now(KST) + timedelta(days=1)
    created = await mock_flow.create_task("보고서", original_due)
    set_decision(monkeypatch, Decision(
        transcript="보고서 마감 미뤄줘", action="update_due",
        args=DecisionArgs(item_id=created.item_id, due_at=original_due + timedelta(days=1)),
    ))

    async def fail_update(item_id, due_at):
        raise EurekaError("test failure")

    monkeypatch.setattr(mock_flow, "update_due", fail_update)
    response = await tasks.handle(user_id="pet-01", request_id="req-14", text="보고서 마감 미뤄줘")
    assert response.status == "ERROR"
    assert "변경했어요" not in response.output.tts
    assert mock_flow.tasks[created.item_id].due_at == original_due


def create_decision(transcript, name, due_at=None):
    return Decision(
        transcript=transcript, action="create_task", args=DecisionArgs(name=name, due_at=due_at),
    )


@pytest.mark.parametrize("from_days, to_days", [(0, 0), (-1, 6), (None, None)])
async def test_list_does_not_read_past_due_tasks(mock_flow, monkeypatch, from_days, to_days):
    today = datetime.now(KST).date()

    def shift(days):
        return None if days is None else today + timedelta(days=days)

    freeze_now(monkeypatch, datetime.combine(today, time(12), KST))
    await mock_flow.create_task("지난 업무", datetime.combine(today, time(9), KST))
    await mock_flow.create_task("어제 업무", datetime.combine(today - timedelta(days=1), time(9), KST))
    await mock_flow.create_task("남은 업무", datetime.combine(today, time(15), KST))
    set_decision(monkeypatch, Decision(
        transcript="업무 알려줘", action="list_tasks",
        args=DecisionArgs(date_from=shift(from_days), date_to=shift(to_days)),
    ))

    response = await tasks.handle(user_id="pet-01", request_id="req-65", text="업무 알려줘")

    assert "남은 업무" in response.output.tts
    assert "지난 업무" not in response.output.tts
    assert "어제 업무" not in response.output.tts
    assert response.output.lcd.text == "업무 1건"
    assert len(await mock_flow.list_open_tasks()) == 3


async def test_past_due_task_can_still_be_completed(mock_flow, monkeypatch):
    created = await mock_flow.create_task("지난 업무", datetime.now(KST) - timedelta(days=1))
    set_decision(monkeypatch, Decision(
        transcript="지난 업무 끝났어", action="complete_task", args=DecisionArgs(item_id=created.item_id),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-66", text="지난 업무 끝났어")
    assert response.type == "task_completed"


async def test_list_reads_nearest_due_first_even_when_truncated(mock_flow, monkeypatch):
    base = datetime.now(KST) + timedelta(days=30)
    for offset in [6, 2, 5, 1, 4, 3]:
        await mock_flow.create_task(f"업무{offset}", base + timedelta(days=offset))
    set_decision(monkeypatch, Decision(
        transcript="전체 업무", action="list_tasks", args=DecisionArgs(),
    ))

    response = await tasks.handle(user_id="pet-01", request_id="req-62", text="전체 업무")

    tts = response.output.tts
    positions = [tts.index(f"업무{n}") for n in [1, 2, 3, 4, 5]]
    assert positions == sorted(positions)
    assert "업무6" not in tts
    assert "외 1개" in tts


@pytest.mark.parametrize("hour, minute, created, updated", [
    (15, 0, "오후 3시예요.", "오후 3시로 변경했어요."),
    (23, 59, "오후 11시 59분이에요.", "오후 11시 59분으로 변경했어요."),
    (11, 30, "오전 11시 30분이에요.", "오전 11시 30분으로 변경했어요."),
])
async def test_due_phrases_use_correct_particles(mock_flow, monkeypatch, hour, minute, created, updated):
    day = datetime.now(KST).date() + timedelta(days=2)
    due = datetime.combine(day, time(hour, minute), KST)
    set_decision(monkeypatch, create_decision("보고서 추가", "보고서", due))
    response = await tasks.handle(user_id="pet-01", request_id="req-63", text="보고서 추가")
    assert response.output.tts.endswith(created)

    item_id = next(iter(mock_flow.tasks))
    set_decision(monkeypatch, Decision(
        transcript="보고서 마감 변경", action="update_due", args=DecisionArgs(item_id=item_id, due_at=due),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-64", text="보고서 마감 변경")
    assert response.output.tts.endswith(updated)
async def ask_to_delete(mock_flow, monkeypatch, user_id="pet-01"):
    task = await mock_flow.create_task("보고서 작성", datetime.now(KST) + timedelta(days=1))
    set_decision(monkeypatch, Decision(
        transcript="보고서 작성 삭제해줘", action="delete_task", args=DecisionArgs(item_id=task.item_id),
    ))
    response = await tasks.handle(user_id=user_id, request_id="req-40", text="보고서 작성 삭제해줘")
    return task, response


def answer_decision(text, action="clarify"):
    return Decision(transcript=text, action=action, args=DecisionArgs(), question="무엇을 할까요?")


async def test_first_delete_request_only_asks(mock_flow, monkeypatch):
    task, response = await ask_to_delete(mock_flow, monkeypatch)
    assert response.type == "clarification"
    assert "보고서 작성 업무를 삭제할까요?" in response.output.tts
    assert "마감은" in response.output.tts
    assert task.item_id in mock_flow.tasks
    assert db.get_pending("pet-01")["delete_item_id"] == task.item_id


def confirm_decision(text, item_id, confirm):
    return Decision(
        transcript=text, action="delete_task", args=DecisionArgs(item_id=item_id, confirm=confirm),
    )


@pytest.mark.parametrize("answer", ["응", "응응", "알겠어요", "그래 지워"])
async def test_ai_yes_deletes_task_and_reminder(mock_flow, monkeypatch, answer):
    task, _ = await ask_to_delete(mock_flow, monkeypatch)
    db.set_reminder(task.item_id, 60)
    set_decision(monkeypatch, confirm_decision(answer, task.item_id, "yes"))

    response = await tasks.handle(user_id="pet-01", request_id="req-41", text=answer)

    assert response.status == "OK"
    assert response.type == "task_deleted"
    assert response.output.tts == "보고서 작성 업무를 삭제했어요."
    assert mock_flow.tasks == {}
    assert db.get_pending("pet-01") is None
    with db.connect() as conn:
        assert conn.execute("SELECT 1 FROM reminders WHERE item_id = ?", (task.item_id,)).fetchone() is None


async def test_ai_no_cancels_delete(mock_flow, monkeypatch):
    task, _ = await ask_to_delete(mock_flow, monkeypatch)
    set_decision(monkeypatch, confirm_decision("아 그냥 둬", task.item_id, "no"))
    response = await tasks.handle(user_id="pet-01", request_id="req-42", text="아 그냥 둬")
    assert response.type == "task_delete_cancelled"
    assert task.item_id in mock_flow.tasks
    assert db.get_pending("pet-01") is None


async def test_unclear_answer_asks_again_for_same_task(mock_flow, monkeypatch):
    task, _ = await ask_to_delete(mock_flow, monkeypatch)
    set_decision(monkeypatch, confirm_decision("음", task.item_id, None))
    response = await tasks.handle(user_id="pet-01", request_id="req-52", text="음")
    assert response.type == "clarification"
    assert "보고서 작성 업무를 삭제할까요?" in response.output.tts
    assert task.item_id in mock_flow.tasks
    assert db.get_pending("pet-01")["delete_item_id"] == task.item_id


async def test_yes_for_another_task_does_not_delete_either(mock_flow, monkeypatch):
    """AI가 동의라고 판단해도 확인 질문의 대상이 아닌 업무면 지우지 않고, 그 업무를 다시 확인한다."""
    task, _ = await ask_to_delete(mock_flow, monkeypatch)
    other = await mock_flow.create_task("발표 준비", datetime.now(KST) + timedelta(days=2))
    set_decision(monkeypatch, confirm_decision("응", other.item_id, "yes"))
    response = await tasks.handle(user_id="pet-01", request_id="req-53", text="응")
    assert response.type == "clarification"
    assert "발표 준비 업무를 삭제할까요?" in response.output.tts
    assert task.item_id in mock_flow.tasks and other.item_id in mock_flow.tasks
    assert db.get_pending("pet-01")["delete_item_id"] == other.item_id


async def test_yes_without_confirmation_question_only_asks(mock_flow, monkeypatch):
    task = await mock_flow.create_task("보고서 작성", datetime.now(KST) + timedelta(days=1))
    set_decision(monkeypatch, confirm_decision("보고서 작성 지워", task.item_id, "yes"))
    response = await tasks.handle(user_id="pet-01", request_id="req-54", text="보고서 작성 지워")
    assert response.type == "clarification"
    assert task.item_id in mock_flow.tasks


async def test_ai_saying_delete_again_does_not_execute(mock_flow, monkeypatch):
    task, _ = await ask_to_delete(mock_flow, monkeypatch)
    response = await tasks.handle(user_id="pet-01", request_id="req-43", text="보고서 작성 삭제해줘")
    assert response.type == "clarification"
    assert task.item_id in mock_flow.tasks


async def test_echoed_transcript_on_voice_does_not_delete(mock_flow, monkeypatch):
    task, _ = await ask_to_delete(mock_flow, monkeypatch)
    set_decision(monkeypatch, Decision(
        transcript="보고서 작성 삭제해줘", action="delete_task", args=DecisionArgs(item_id=task.item_id),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-44", audio=b"wav")
    assert response.type == "clarification"
    assert task.item_id in mock_flow.tasks


async def test_other_request_cancels_pending_delete(mock_flow, monkeypatch):
    task, _ = await ask_to_delete(mock_flow, monkeypatch)
    set_decision(monkeypatch, Decision(
        transcript="오늘 할 일", action="list_tasks", args=DecisionArgs(),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-45", text="오늘 할 일")
    assert response.type == "task_list"
    assert db.get_pending("pet-01") is None

    set_decision(monkeypatch, confirm_decision("응", task.item_id, "yes"))
    response = await tasks.handle(user_id="pet-01", request_id="req-46", text="응")
    assert response.type == "clarification"
    assert task.item_id in mock_flow.tasks


async def test_reminder_request_also_cancels_pending_delete(mock_flow, monkeypatch):
    task, _ = await ask_to_delete(mock_flow, monkeypatch)
    set_decision(monkeypatch, Decision(
        transcript="알림 바꿔줘", action="set_reminder",
        args=DecisionArgs(item_id=task.item_id, offset_minutes=30),
    ))
    await tasks.handle(user_id="pet-01", request_id="req-47", text="알림 바꿔줘")
    assert db.get_pending("pet-01") is None
    assert task.item_id in mock_flow.tasks


async def test_delete_unknown_task_clarifies_without_delete(mock_flow, monkeypatch):
    set_decision(monkeypatch, Decision(
        transcript="그거 삭제해줘", action="delete_task", args=DecisionArgs(item_id="unknown"),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-48", text="그거 삭제해줘")
    assert response.type == "clarification"
    assert response.output.tts == "어떤 업무를 삭제할까요?"
    assert "delete_item_id" not in db.get_pending("pet-01")


async def test_confirm_when_task_is_gone_reports_error(mock_flow, monkeypatch):
    task, _ = await ask_to_delete(mock_flow, monkeypatch)
    await mock_flow.complete_task(task.item_id)
    set_decision(monkeypatch, confirm_decision("응", task.item_id, "yes"))
    response = await tasks.handle(user_id="pet-01", request_id="req-49", text="응")
    assert response.status == "ERROR"
    assert response.output.tts == "삭제할 업무를 찾지 못했어요."
    assert task.item_id in mock_flow.tasks


async def test_eureka_failure_does_not_claim_delete_and_clears_pending(mock_flow, monkeypatch):
    task, _ = await ask_to_delete(mock_flow, monkeypatch)
    set_decision(monkeypatch, confirm_decision("응", task.item_id, "yes"))

    async def fail_delete(item_id):
        raise EurekaError("test failure")

    monkeypatch.setattr(mock_flow, "delete_task", fail_delete)
    response = await tasks.handle(user_id="pet-01", request_id="req-50", text="응")
    assert response.status == "ERROR"
    assert "삭제했어요" not in response.output.tts
    assert db.get_pending("pet-01") is None


async def test_delete_marker_is_not_passed_to_ai(mock_flow, monkeypatch):
    await ask_to_delete(mock_flow, monkeypatch)
    received = []

    async def analyze(**kwargs):
        received.append(kwargs["pending"])
        return answer_decision("네")

    monkeypatch.setattr(tasks.ai, "analyze", analyze)
    await tasks.handle(user_id="pet-01", request_id="req-51", text="네")
    assert "delete_item_id" not in received[0]
    assert "utterances" not in received[0]


CHECK_NOW = datetime(2026, 10, 8, 10, 0, tzinfo=KST)
CHECK_TASKS = [Task(item_id="t1", name="보고서 검토", due_at=CHECK_NOW + timedelta(days=1), status="todo")]


@pytest.mark.parametrize(
    ("action", "args", "blocked"),
    [
        ("complete_task", {"item_id": "t1"}, None),
        ("complete_task", {"item_id": "t9"}, tasks.Blocked("unknown_task")),
        ("complete_task", {}, tasks.Blocked("unknown_task")),
        ("delete_task", {"item_id": "t9"}, tasks.Blocked("unknown_task")),
        ("update_due", {"item_id": "t1"}, tasks.Blocked("missing", ("due_at",))),
        ("update_due", {"item_id": "t1", "due_at": CHECK_NOW - timedelta(hours=1)}, tasks.Blocked("past_due")),
        ("update_due", {"item_id": "t1", "due_at": datetime(2026, 10, 9, 15)}, tasks.Blocked("past_due")),
        ("create_task", {"due_at": CHECK_NOW + timedelta(days=1)}, tasks.Blocked("missing", ("name",))),
        ("create_task", {"name": "  ", "due_at": CHECK_NOW + timedelta(days=1)}, tasks.Blocked("missing", ("name",))),
        ("create_task", {"name": "보고서"}, tasks.Blocked("missing", ("due_at",))),
        ("create_task", {}, tasks.Blocked("missing", ("name", "due_at"))),
        (
            "create_task",
            {"name": "보고서", "due_at": CHECK_NOW + timedelta(days=1), "offset_minutes": 0},
            tasks.Blocked("bad_offset"),
        ),
        ("create_task", {"name": "보고서", "due_at": CHECK_NOW + timedelta(days=1), "offset_minutes": 30}, None),
        ("list_tasks", {}, None),
        ("set_reminder", {"item_id": "t1", "reminder_enabled": False}, None),
        ("set_reminder", {"item_id": "t1", "reminder_enabled": True}, None),
        ("set_reminder", {"item_id": "t1"}, tasks.Blocked("missing", ("offset_minutes",))),
    ],
)
def test_check_decision_reports_only_execution_preconditions(action, args, blocked):
    decision = Decision(transcript="", action=action, args=DecisionArgs(**args))
    assert tasks.check_decision(decision, CHECK_TASKS, CHECK_NOW) == blocked


@pytest.mark.parametrize(
    ("fields", "question"),
    [
        (("name",), "업무 이름을 알려 주세요."),
        (("due_at",), "마감 시간을 알려 주세요."),
        (("name", "due_at"), "업무 이름과 마감 시간을 알려 주세요."),
    ],
)
def test_missing_fields_are_asked_at_once(fields, question):
    assert tasks._check_question("create_task", tasks.Blocked("missing", fields)) == question


async def test_set_reminder_saves_offset_for_pi(mock_flow, monkeypatch):
    task = await mock_flow.create_task("보고서 검토", datetime.now(KST) + timedelta(days=1))
    db.set_reminder(task.item_id, 120)
    set_decision(monkeypatch, Decision(
        transcript="보고서 검토 알림 30분 전으로 바꿔줘", action="set_reminder",
        args=DecisionArgs(item_id=task.item_id, offset_minutes=30),
    ))

    response = await tasks.handle(user_id="pet-01", request_id="req-70", text="보고서 검토 알림 30분 전으로 바꿔줘")

    assert response.status == "OK"
    assert response.type == "reminder_set"
    assert response.output.tts == "보고서 검토 업무 알림을 마감 30분 전으로 바꿨어요."
    assert db.get_reminders()[task.item_id] == 30
    device_tasks = await tasks.list_device_tasks()
    assert [t.offset_minutes for t in device_tasks.tasks] == [30]


def reminder_decision(transcript, item_id, **args):
    return Decision(transcript=transcript, action="set_reminder", args=DecisionArgs(item_id=item_id, **args))


async def test_reminder_off_keeps_offset_and_flags_pi(mock_flow, monkeypatch):
    task = await mock_flow.create_task("보고서 검토", datetime.now(KST) + timedelta(days=1))
    db.set_reminder(task.item_id, 120)
    set_decision(monkeypatch, reminder_decision("보고서 검토 알림 꺼줘", task.item_id, reminder_enabled=False))

    response = await tasks.handle(user_id="pet-01", request_id="req-80", text="보고서 검토 알림 꺼줘")

    assert response.type == "reminder_set"
    assert response.output.tts == "보고서 검토 업무 알림을 껐어요."
    assert response.output.lcd.text == "알림 끔"
    assert db.get_reminders()[task.item_id] == 120
    [device_task] = (await tasks.list_device_tasks()).tasks
    assert device_task.reminder_enabled is False
    assert device_task.offset_minutes == 120


async def test_reminder_on_restores_saved_offset(mock_flow, monkeypatch):
    task = await mock_flow.create_task("보고서 검토", datetime.now(KST) + timedelta(days=1))
    db.set_reminder(task.item_id, 120)
    db.set_reminder_enabled(task.item_id, False, 60)
    set_decision(monkeypatch, reminder_decision("보고서 검토 알림 다시 켜줘", task.item_id, reminder_enabled=True))

    response = await tasks.handle(user_id="pet-01", request_id="req-81", text="보고서 검토 알림 다시 켜줘")

    assert response.output.tts == "보고서 검토 업무 알림을 다시 켰어요. 마감 2시간 전에 알려 드려요."
    assert response.output.lcd.text == "알림 켬"
    [device_task] = (await tasks.list_device_tasks()).tasks
    assert device_task.reminder_enabled is True
    assert device_task.offset_minutes == 120


async def test_reminder_on_without_saved_row_uses_default(mock_flow, monkeypatch):
    monkeypatch.setattr(settings, "default_reminder_minutes", 60)
    task = await mock_flow.create_task("보고서 검토", datetime.now(KST) + timedelta(days=1))
    set_decision(monkeypatch, reminder_decision("알림 켜줘", task.item_id, reminder_enabled=True))

    response = await tasks.handle(user_id="pet-01", request_id="req-82", text="알림 켜줘")

    assert response.output.tts == "보고서 검토 업무 알림을 다시 켰어요. 마감 1시간 전에 알려 드려요."
    assert db.get_reminders()[task.item_id] == 60


async def test_changing_offset_turns_reminder_back_on(mock_flow, monkeypatch):
    task = await mock_flow.create_task("보고서 검토", datetime.now(KST) + timedelta(days=1))
    db.set_reminder(task.item_id, 120)
    db.set_reminder_enabled(task.item_id, False, 60)
    set_decision(monkeypatch, reminder_decision("알림 30분 전으로 바꿔줘", task.item_id, offset_minutes=30))

    response = await tasks.handle(user_id="pet-01", request_id="req-83", text="알림 30분 전으로 바꿔줘")

    assert response.output.tts == "보고서 검토 업무 알림을 마감 30분 전으로 바꿨어요."
    [device_task] = (await tasks.list_device_tasks()).tasks
    assert device_task.reminder_enabled is True
    assert device_task.offset_minutes == 30


async def test_create_without_reminder(mock_flow, monkeypatch):
    monkeypatch.setattr(settings, "default_reminder_minutes", 60)
    set_decision(monkeypatch, Decision(
        transcript="알림 없이 회의 등록해줘", action="create_task",
        args=DecisionArgs(name="회의", due_at=datetime.now(KST) + timedelta(days=1), reminder_enabled=False),
    ))

    response = await tasks.handle(user_id="pet-01", request_id="req-84", text="알림 없이 회의 등록해줘")

    assert response.type == "task_created"
    assert response.output.tts.endswith("알림은 꺼 두었어요.")
    [device_task] = (await tasks.list_device_tasks()).tasks
    assert device_task.reminder_enabled is False
    assert device_task.offset_minutes == 60


@pytest.mark.parametrize(("args", "question"), [
    ({"item_id": "t9", "offset_minutes": 30}, "어떤 업무의 알림을 바꿀까요?"),
    ({"offset_minutes": 30}, "어떤 업무의 알림을 바꿀까요?"),
    ({"item_id": "KNOWN"}, "알림 시간을 알려 주세요."),
    ({"item_id": "KNOWN", "offset_minutes": 0}, "알림 시간을 1분 이상으로 알려 주세요."),
])
async def test_set_reminder_clarifies_without_saving(mock_flow, monkeypatch, args, question):
    task = await mock_flow.create_task("보고서 검토", datetime.now(KST) + timedelta(days=1))
    if args.get("item_id") == "KNOWN":
        args = {**args, "item_id": task.item_id}
    set_decision(monkeypatch, Decision(transcript="알림 바꿔줘", action="set_reminder", args=DecisionArgs(**args)))

    response = await tasks.handle(user_id="pet-01", request_id="req-71", text="알림 바꿔줘")

    assert response.type == "clarification"
    assert response.output.tts == question
    assert task.item_id not in db.get_reminders()


async def test_set_reminder_db_failure_does_not_claim_success(mock_flow, monkeypatch):
    task = await mock_flow.create_task("보고서 검토", datetime.now(KST) + timedelta(days=1))
    set_decision(monkeypatch, Decision(
        transcript="알림 30분 전", action="set_reminder",
        args=DecisionArgs(item_id=task.item_id, offset_minutes=30),
    ))

    def fail(*args):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(tasks.db, "set_reminder", fail)
    response = await tasks.handle(user_id="pet-01", request_id="req-72", text="알림 30분 전")
    assert response.status == "ERROR"
    assert "바꿨어요" not in response.output.tts


@pytest.mark.parametrize(("minutes", "text"), [
    (30, "30분"), (60, "1시간"), (90, "1시간 30분"), (1440, "1일"), (1530, "1일 1시간 30분"),
])
def test_format_offset(minutes, text):
    assert tasks._format_offset(minutes) == text
