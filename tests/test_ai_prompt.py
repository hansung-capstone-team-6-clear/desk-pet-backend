"""Gemini에 보내는 프롬프트가 Decision과 어긋나지 않는지 검증한다. 실제 Gemini는 호출하지 않는다."""

from typing import get_args

from desk_pet_backend.schemas import Action
from desk_pet_backend.services import ai


def test_prompt_lists_every_action():
    for action in get_args(Action):
        assert action in ai.SYSTEM_PROMPT


def test_prompt_keeps_transcript_to_current_utterance():
    assert "pending.transcript나 이전 발화를 앞뒤에 붙이거나 합치지 않는다" in ai.SYSTEM_PROMPT


def test_prompt_asks_for_delete_confirm():
    """tasks.py는 삭제 확인 답을 args.confirm으로 판단한다 (PR #39). 프롬프트가 confirm 값을 모두 설명해야 한다."""
    assert "confirm" in ai.SYSTEM_PROMPT
    for value in ('confirm "yes"', 'confirm "no"', "confirm null"):
        assert value in ai.SYSTEM_PROMPT
