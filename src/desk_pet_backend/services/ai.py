"""LLM 1회 호출 → Decision (contracts § 3). 담당: ai.

모델을 바꿀 때는 이 파일과 config의 llm_* 값만 바뀌어야 한다.
"""

from datetime import datetime

from desk_pet_backend.schemas import Decision, Task


async def analyze(
    *,
    audio: bytes | None,
    text: str | None,
    now: datetime,
    open_tasks: list[Task],
    pending: dict | None,
) -> Decision:
    """오디오 또는 텍스트를 맥락과 함께 LLM에 한 번 보내고, 응답 JSON을 Decision으로 검증해 돌려준다.

    프롬프트에 넣는 것 (knowledge 레포 projects/eureka-desk-pet/ai/README.md):
    1. now (+09:00, 요일 포함)
    2. open_tasks 최대 30개 — "보고서 검토 완료했어"의 대상을 찾는 근거
    3. pending — 재질문 중이면 원래 요청
    4. 규칙: 모호하면 clarify, 날짜를 지어내지 않음, 알림 시점을 말하지 않았으면 offset_minutes = null

    LLM 응답이 스키마에 맞지 않으면 Decision.model_validate_json()이 ValidationError를 낸다.
    """
    # TODO(ai, 10/18): 프롬프트 작성 → 모델 호출 → Decision.model_validate_json(응답)
    raise NotImplementedError
