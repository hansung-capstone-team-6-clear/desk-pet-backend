"""AI 판단 뒤 등록·조회 정책과 응답을 검증한다. 실제 Eureka는 호출하지 않는다."""

from datetime import datetime, time, timedelta

import pytest

from desk_pet_backend import db
from desk_pet_backend.config import settings
from desk_pet_backend.eureka import KST, EurekaError, EurekaMock
from desk_pet_backend.schemas import Decision, DecisionArgs
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
    response = await tasks.handle(user_id="pet-01", request_id="req-2", text="업무 등록")
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
    response = await tasks.handle(user_id="pet-01", request_id="req-3", text="어제 업무 등록")
    assert response.type == "clarification"
    assert response.status == "OK"
    assert await mock_flow.list_open_tasks() == []


async def test_list_filters_by_kst_date_and_hides_done(mock_flow, monkeypatch):
    today = datetime.now(KST).date()
    today_task = await mock_flow.create_task("오늘 업무", datetime.combine(today, time(15), KST))
    await mock_flow.create_task("내일 업무", datetime.combine(today + timedelta(days=1), time(15), KST))
    done = await mock_flow.create_task("종료된 보고서", datetime.combine(today, time(16), KST))
    await mock_flow.complete_task(done.item_id)
    set_decision(monkeypatch, Decision(
        transcript="오늘 할 일", action="list_tasks", args=DecisionArgs(range="today"),
    ))

    response = await tasks.handle(user_id="pet-01", request_id="req-4", text="오늘 할 일")

    assert response.status == "OK"
    assert response.type == "task_list"
    assert "오늘 업무" in response.output.tts
    assert "내일 업무" not in response.output.tts
    assert "종료된 보고서" not in response.output.tts
    assert response.output.lcd.text == "업무 1건"
    assert today_task.item_id in mock_flow.tasks


@pytest.mark.parametrize("range_name, expected", [
    ("today", ["today"]), ("tomorrow", ["tomorrow"]),
    ("week", ["today", "tomorrow"]), ("all", ["today", "tomorrow", "next_week"]),
])
def test_date_ranges_use_korean_week(range_name, expected):
    now = datetime(2026, 10, 28, 12, tzinfo=KST)  # 수요일
    from desk_pet_backend.schemas import Task

    items = [
        Task(item_id="1", name="today", due_at=datetime(2026, 10, 28, 15, tzinfo=KST), status="todo"),
        Task(item_id="2", name="tomorrow", due_at=datetime(2026, 10, 29, 15, tzinfo=KST), status="todo"),
        Task(item_id="3", name="next_week", due_at=datetime(2026, 11, 2, 15, tzinfo=KST), status="todo"),
    ]
    assert [task.name for task in tasks._filter_tasks(items, range_name, now)] == expected


async def test_eureka_failure_does_not_claim_create_success(mock_flow, monkeypatch):
    set_decision(monkeypatch, Decision(
        transcript="업무 등록", action="create_task",
        args=DecisionArgs(name="보고서", due_at=datetime.now(KST) + timedelta(days=1)),
    ))

    async def fail_create(name, due_at):
        raise EurekaError("test failure")

    monkeypatch.setattr(mock_flow, "create_task", fail_create)
    response = await tasks.handle(user_id="pet-01", request_id="req-5", text="업무 등록")
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
