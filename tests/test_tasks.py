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
    assert db.get_pending("pet-01") == {
        **first.model_dump(mode="json"),
        "utterances": ["보고서 등록해줘"],
    }
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
    (True, None, "보고서 업무의 마감을 언제로 변경할까요?"),
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


@pytest.mark.parametrize("text", [
    "내일 오후 3시까지 할 일 하나 추가해줘",
    "보고서 확인하고 작성 일정도 알려줘. 새 업무는 내일 추가해 줘.",
])
async def test_name_borrowed_from_task_list_is_not_created(mock_flow, monkeypatch, text):
    await mock_flow.create_task("보고서 작성", datetime.now(KST) + timedelta(days=5))
    due = datetime.now(KST) + timedelta(days=1)
    set_decision(monkeypatch, create_decision(text, "보고서 작성", due))

    response = await tasks.handle(user_id="pet-01", request_id="req-20", text=text)

    assert response.type == "clarification"
    assert response.output.tts == "어떤 일을 추가할까요?"
    assert len(mock_flow.tasks) == 1
    pending = db.get_pending("pet-01")
    assert pending["args"]["name"] is None
    assert pending["args"]["due_at"] is not None


@pytest.mark.parametrize("text", [
    "보고서 작성 추가해 줘", "보고서 작성을 추가해 줘", "보고서작성 추가해 줘",
])
async def test_same_name_as_open_task_passes_when_spoken_continuously(mock_flow, monkeypatch, text):
    await mock_flow.create_task("보고서 작성", datetime.now(KST) + timedelta(days=5))
    set_decision(monkeypatch, create_decision(
        text, "보고서 작성", datetime.now(KST) + timedelta(days=1),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-21", text=text)
    assert response.type == "task_created"
    assert len(mock_flow.tasks) == 2


async def test_name_without_any_source_is_not_created(mock_flow, monkeypatch):
    text = "내일까지 할 일 추가해줘"
    set_decision(monkeypatch, create_decision(
        text, "팀 회의", datetime.now(KST) + timedelta(days=1),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-22", text=text)
    assert response.type == "clarification"
    assert mock_flow.tasks == {}


async def test_known_limitation_combined_words_pass_name_policy(mock_flow, monkeypatch):
    """단어를 조합한 이름은 정책상 통과한다. 정상 동작이 아니라 알려진 한계이며 AI 평가에서 다룬다."""
    text = "보고서랑 발표 준비해"
    set_decision(monkeypatch, create_decision(
        text, "발표 보고서 준비", datetime.now(KST) + timedelta(days=1),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-23", text=text)
    assert response.type == "task_created"


async def test_first_utterance_is_kept_through_repeated_clarification(mock_flow, monkeypatch):
    due = datetime.now(KST) + timedelta(days=1)
    turns = [
        ("보고서 작성 추가해줘", Decision(
            transcript="보고서 작성 추가해줘", action="clarify",
            args=DecisionArgs(name="보고서 작성"), question="마감은 언제인가요?",
        )),
        ("내일", Decision(
            transcript="내일", action="clarify",
            args=DecisionArgs(name="보고서 작성"), question="몇 시까지인가요?",
        )),
        ("오후", Decision(
            transcript="오후", action="clarify",
            args=DecisionArgs(name="보고서 작성"), question="몇 시인지 알려 주세요.",
        )),
        ("세 시쯤", Decision(
            transcript="세 시쯤", action="clarify",
            args=DecisionArgs(name="보고서 작성"), question="정확한 시간을 알려 주세요.",
        )),
    ]
    received = []
    for text, decision in turns:
        async def analyze(decision=decision, **kwargs):
            received.append(kwargs["pending"])
            return decision

        monkeypatch.setattr(tasks.ai, "analyze", analyze)
        await tasks.handle(user_id="pet-01", request_id="req-24", text=text)

    assert db.get_pending("pet-01")["utterances"] == ["보고서 작성 추가해줘", "오후", "세 시쯤"]
    assert all(pending is None or "utterances" not in pending for pending in received)

    set_decision(monkeypatch, create_decision("오후 세 시", "보고서 작성", due))
    response = await tasks.handle(user_id="pet-01", request_id="req-25", text="오후 세 시")
    assert response.type == "task_created"
    assert db.get_pending("pet-01") is None


async def test_correct_name_after_failed_validation_creates_task(mock_flow, monkeypatch):
    await mock_flow.create_task("보고서 작성", datetime.now(KST) + timedelta(days=5))
    due = datetime.now(KST) + timedelta(days=1)
    first = "내일 오후 3시까지 할 일 하나 추가해줘"
    set_decision(monkeypatch, create_decision(first, "보고서 작성", due))
    response = await tasks.handle(user_id="pet-01", request_id="req-26", text=first)
    assert response.type == "clarification"

    set_decision(monkeypatch, create_decision("과제 제출", "과제 제출", due))
    response = await tasks.handle(user_id="pet-01", request_id="req-27", text="과제 제출")
    assert response.type == "task_created"
    assert "과제 제출" in response.output.tts
    assert db.get_pending("pet-01") is None


async def test_complete_clarification_is_not_a_name_source(mock_flow, monkeypatch):
    await mock_flow.create_task("보고서 작성", datetime.now(KST) + timedelta(days=5))
    spoken = "보고서 작성 끝난 것 같아"
    set_decision(monkeypatch, Decision(
        transcript=spoken, action="complete_task", args=DecisionArgs(),
    ))
    await tasks.handle(user_id="pet-01", request_id="req-28", text=spoken)

    set_decision(monkeypatch, create_decision(
        "내일까지", "보고서 작성", datetime.now(KST) + timedelta(days=1),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-29", text="내일까지")
    assert response.type == "clarification"
    assert len(mock_flow.tasks) == 1


@pytest.mark.parametrize("name", [
    "할 일", "할일", "해야 할 일", "업무", "일정", "새 업무", "할 일 하나", "일정 추가",
    "내일 오후 3시", "내일 오후 3시까지", "오후 세 시", "11월 20일", "다음 주 월요일", "오늘", "마감",
])
async def test_generic_or_date_only_name_is_not_created(mock_flow, monkeypatch, name):
    due = datetime.now(KST) + timedelta(days=1)
    spoken = f"{name} 추가해줘"
    set_decision(monkeypatch, create_decision(spoken, name, due))

    response = await tasks.handle(user_id="pet-01", request_id="req-60", text=spoken)

    assert response.type == "clarification"
    assert response.output.tts == "어떤 일을 추가할까요?"
    assert mock_flow.tasks == {}
    pending = db.get_pending("pet-01")
    assert pending["args"]["name"] is None
    assert pending["args"]["due_at"] is not None


@pytest.mark.parametrize("name", [
    "회의", "시험", "오늘 회의 준비", "내일 발표", "일정 조율", "업무 보고", "할 일 정리", "일본어 공부",
    "3월 보고서", "등록금 납부", "하나은행 방문", "이메일", "반 모임", "3시 회의",
])
async def test_real_names_containing_noise_words_are_created(mock_flow, monkeypatch, name):
    spoken = f"{name} 추가해줘"
    set_decision(monkeypatch, create_decision(spoken, name, datetime.now(KST) + timedelta(days=1)))
    response = await tasks.handle(user_id="pet-01", request_id="req-61", text=spoken)
    assert response.type == "task_created"


@pytest.mark.parametrize("range_name", ["today", "week", "all"])
async def test_list_does_not_read_past_due_tasks(mock_flow, monkeypatch, range_name):
    today = datetime.now(KST).date()
    freeze_now(monkeypatch, datetime.combine(today, time(12), KST))
    await mock_flow.create_task("지난 업무", datetime.combine(today, time(9), KST))
    await mock_flow.create_task("어제 업무", datetime.combine(today - timedelta(days=1), time(9), KST))
    await mock_flow.create_task("남은 업무", datetime.combine(today, time(15), KST))
    set_decision(monkeypatch, Decision(
        transcript="업무 알려줘", action="list_tasks", args=DecisionArgs(range=range_name),
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
        transcript="전체 업무", action="list_tasks", args=DecisionArgs(range="all"),
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


async def test_old_pending_without_utterances_falls_back_to_transcript(mock_flow, monkeypatch):
    db.set_pending("pet-01", Decision(
        transcript="보고서 작성 추가해줘", action="clarify",
        args=DecisionArgs(name="보고서 작성"), question="마감은 언제인가요?",
    ).model_dump(mode="json"))
    set_decision(monkeypatch, create_decision(
        "내일까지", "보고서 작성", datetime.now(KST) + timedelta(days=1),
    ))
    response = await tasks.handle(user_id="pet-01", request_id="req-30", text="내일까지")
    assert response.type == "task_created"
