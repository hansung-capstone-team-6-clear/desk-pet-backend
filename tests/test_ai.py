"""Gemini 503 재시도 정책을 검증한다. 실제 Gemini는 호출하지 않는다."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from google.genai import errors
from pydantic import ValidationError

from desk_pet_backend.eureka import KST, EurekaMock
from desk_pet_backend.services import ai, tasks

VALID_JSON = (
    '{"transcript": "오늘 할 일", "action": "list_tasks", '
    '"args": {"range": "today"}, "question": null}'
)


def api_error(code):
    return errors.APIError(code, {"error": {"code": code, "message": "x", "status": "x"}})


@pytest.fixture
def gemini(monkeypatch):
    """generate_content가 outcomes를 차례로 돌려주거나 던지게 한다. 호출 수를 calls에 남긴다."""
    state = SimpleNamespace(outcomes=[], calls=0)

    async def generate_content(**kwargs):
        state.calls += 1
        outcome = state.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(text=outcome)

    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
        generate_content=generate_content,
    )))
    monkeypatch.setattr(ai.genai, "Client", lambda **kwargs: client)
    monkeypatch.setattr(ai, "RETRY_DELAY_SECONDS", 0)
    return state


async def analyze():
    return await ai.analyze(
        audio=None, text="오늘 할 일", now=datetime.now(KST), open_tasks=[], pending=None
    )


async def test_success_calls_gemini_once(gemini):
    gemini.outcomes = [VALID_JSON]
    assert (await analyze()).action == "list_tasks"
    assert gemini.calls == 1


async def test_503_is_retried_once_and_can_succeed(gemini):
    gemini.outcomes = [api_error(503), VALID_JSON]
    assert (await analyze()).action == "list_tasks"
    assert gemini.calls == 2


async def test_second_503_is_raised_without_third_call(gemini):
    gemini.outcomes = [api_error(503), api_error(503), VALID_JSON]
    with pytest.raises(errors.APIError):
        await analyze()
    assert gemini.calls == 2


@pytest.mark.parametrize("code", [400, 429, 500])
async def test_other_api_errors_are_not_retried(gemini, code):
    gemini.outcomes = [api_error(code), VALID_JSON]
    with pytest.raises(errors.APIError):
        await analyze()
    assert gemini.calls == 1


@pytest.mark.parametrize("bad", [
    "not json",
    '{transcript: "x"}',  # 실제로 나온 "key must be a string" 형태
    '{"transcript": "x", "action": "unknown", "args": {}, "question": null}',
])
async def test_invalid_decision_json_is_retried_once_and_can_succeed(gemini, bad):
    gemini.outcomes = [bad, VALID_JSON]
    assert (await analyze()).action == "list_tasks"
    assert gemini.calls == 2


async def test_second_invalid_json_is_raised_without_third_call(gemini):
    gemini.outcomes = ["not json", "not json", VALID_JSON]
    with pytest.raises(ValidationError):
        await analyze()
    assert gemini.calls == 2


async def test_retry_budget_is_shared_between_503_and_invalid_json(gemini):
    """503으로 재시도한 뒤 깨진 JSON이 오면 더 재시도하지 않는다 (LLM 재시도 1회 결정)."""
    gemini.outcomes = [api_error(503), "not json", VALID_JSON]
    with pytest.raises(ValidationError):
        await analyze()
    assert gemini.calls == 2


async def test_handle_returns_error_after_second_503(gemini, monkeypatch):
    monkeypatch.setattr(tasks, "get_eureka", lambda: EurekaMock())
    gemini.outcomes = [api_error(503), api_error(503)]

    response = await tasks.handle(user_id="pet-01", request_id="req-1", text="오늘 할 일")

    assert response.status == "ERROR"
    assert response.request_id == "req-1"
    assert gemini.calls == 2
