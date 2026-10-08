"""음성·텍스트 요청 하나를 처리하는 흐름과 정책 (contracts § 3). 담당: backend.

Eureka는 eureka.py를 통해서만, 저장은 db.py를 통해서만 접근한다.
"""

from desk_pet_backend.schemas import VoiceResponse


async def handle(*, device_id: str, audio: bytes | None = None, text: str | None = None) -> VoiceResponse:
    """요청 하나를 처리해 Pi에 돌려줄 응답을 만든다.

    1. Eureka 미완료 업무 목록 + db의 pending(재질문 대기) 조회
    2. ai.analyze() 1회 호출
    3. 정책 검증 — LLM에 맡기지 않고 여기서 강제한다
       - offsetMinutes가 없으면 settings.default_reminder_minutes (60)
       - itemId가 1번 목록에 없으면 실행하지 않고 clarify
       - dueAt이 과거면 clarify
       - Decision 검증 실패면 error ("다시 한 번 말씀해 주세요")
    4. Eureka 실행 → 2xx와 응답 본문을 확인한 뒤에만 성공 type
    5. 결과를 템플릿 문장으로 만든다 (LLM을 다시 부르지 않는다)
       예: task_created → "{name} 업무를 등록했어요. 마감은 {내일 오후 3시}예요."
    """
    # TODO(backend, 10/18): /text 경로부터 구현 (audio=None)
    raise NotImplementedError
