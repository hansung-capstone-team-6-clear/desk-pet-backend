import json
import os
from typing import Any, Dict, Optional, Union
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 1. Decision / Response Schema 정의 (출력 결과 포격 규격)
# ---------------------------------------------------------------------------
class DecisionArgs(BaseModel):
    name: Optional[str] = Field(default=None, description="업무 이름 (등록 시 사용)")
    due_at: Optional[str] = Field(default=None, description="마감 일시 (ISO 8601 포맷)")
    offset_minutes: Optional[int] = Field(default=None, description="마감 전 알림 시간(분 단위)")
    due_filter: Optional[str] = Field(default=None, description="조회 필터 (today, tomorrow, this_week)")
    item_id: Optional[str] = Field(default=None, description="완료/수정할 기존 업무 ID")
    reason: Optional[str] = Field(default=None, description="재질문 이유")

class Decision(BaseModel):
    transcript: Optional[str] = Field(default=None, description="입력 텍스트")
    action: str = Field(description="수행할 액션")
    args: DecisionArgs = Field(description="액션 인자들")
    question: Optional[str] = Field(default=None, description="재질문 문장")

# ---------------------------------------------------------------------------
# 2. 시스템 프롬프트 설정 (Gemini에게 역할 부여)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """
너는 데스크펫 AI 비서이다. 사용자의 요청과 현재 상태(맥락)를 분석하여 정해진 JSON 포맷으로 Action 및 Args를 도출하라.

[액션 종류]
1. create_task: 새로운 업무 등록 (args: name, due_at, offset_minutes)
2. list_tasks: 업무 목록 조회 (args: due_filter)
3. complete_task: 업무 완료 처리 (args: item_id)
4. update_due_at: 마감 기한 변경 (args: item_id, due_at)
5. update_reminder: 알림 시점 변경 (args: item_id, offset_minutes)
6. clarify: 모호함이나 정보 부족으로 인한 재질문 (args: reason)

[규칙]
- 날짜/시간을 절대 지어내지 말고 주어진 `now` 기준으로 정확히 계산하라.
- 알림 시간이 언급되지 않았으면 `offset_minutes`는 반드시 null로 지정하라.
- 대상 업무를 특정할 수 없거나 모호하면 `clarify` 액션을 사용하라.
- **반드시 하나의 JSON 객체 형태로만 응답하며, 여러 개의 결과를 배열(List) 형태로 감싸서 반환하지 마라.**
"""

def analyze(audio_or_text: Union[str, bytes], context: Dict[str, Any]) -> Decision:
    """
    텍스트 또는 오디오 입력을 받아 Gemini 1회 호출로 의도(Action)와 인자(Args)를 도출한다.
    """
    # .env 파일에서 LLM_API_KEY와 LLM_MODEL을 읽어옴
    api_key = os.environ.get("LLM_API_KEY")
    model_name = os.environ.get("LLM_MODEL", "gemini-2.5-flash-lite")
    
    client = genai.Client(api_key=api_key)

    now_str = context.get("now", "")
    tasks = context.get("tasks", [])
    
    user_context_prompt = f"""
[현재 맥락 정보]
- 기준 시각 (now): {now_str}
- 기존 미완료 업무 목록 (tasks): {json.dumps(tasks, ensure_ascii=False)}

[사용자 요청]
"""

    contents = []
    if isinstance(audio_or_text, str):
        contents.append(user_context_prompt + f'"{audio_or_text}"')
    else:
        contents.append(user_context_prompt)
        contents.append(
            types.Part.from_bytes(data=audio_or_text, mime_type="audio/wav")
        )

    # Gemini API 호출
    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    # Pydantic 모델을 통해 JSON 응답이 올바른지 엄격하게 검증하고 파싱
    return Decision.model_validate_json(response.text)