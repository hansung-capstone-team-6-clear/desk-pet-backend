"""음성·텍스트 요청 하나를 처리하는 흐름과 정책 (contracts § 3). 담당: backend.

Eureka는 eureka.py를 통해서만, 저장은 db.py를 통해서만 접근한다.
"""

from desk_pet_backend.schemas import VoiceResponse


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
    # TODO(backend, 10/18): /api/text 경로부터 구현 (audio=None)
    raise NotImplementedError
