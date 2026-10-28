"""LLM 1회 호출 → Decision (contracts § 3). 담당: ai.

모델을 바꿀 때는 이 파일과 config의 llm_* 값만 바뀌어야 한다.
1차 모델은 Gemini Flash-Lite (knowledge 레포 projects/eureka-desk-pet/ai/README.md).
"""

import json
from datetime import datetime

from google import genai
from google.genai import types

from desk_pet_backend.config import settings
from desk_pet_backend.schemas import Decision, Task

DEFAULT_MODEL = "gemini-2.5-flash-lite"
MAX_TASKS = 30
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

SYSTEM_PROMPT = """
너는 데스크펫 업무 비서다. 사용자 요청과 맥락을 보고 아래 JSON 객체 하나만 출력한다. 배열로 감싸지 않는다.

{"transcript": str, "action": str, "args": {...}, "question": str | null}

- transcript: 사용자가 말한(입력한) 문장 그대로. 오디오면 받아 적은 문장.
- args에는 해당 action이 쓰는 키만 넣는다. 나머지 키는 생략한다.

[action과 args]
1. create_task: name, due_at, offset_minutes
2. list_tasks: range — "today" | "tomorrow" | "week" | "all" 중 하나 ("이번 주" → "week", 기간 언급 없음 → "all")
3. complete_task: item_id
4. update_due: item_id, due_at (마감 변경·연기·당기기)
5. set_reminder: item_id, offset_minutes (알림 설정·변경)
6. clarify: candidates (후보 item_id 배열, 선택) + question 필수 (사용자에게 되물을 짧은 한국어 문장)

[규칙]
- due_at은 +09:00이 붙은 ISO 8601 (예: 2026-10-09T15:00:00+09:00). now를 기준으로 계산한다. 시각 없이 날짜만 말하면 23:59.
- 기존 업무의 날짜만 바꾸면("모레로", "다음 주 수요일로") 원래 due_at의 시각을 그대로 둔다.
- name은 업무를 나타내는 명사구만 쓴다. "끝내기", "해야 해" 같은 동사·어미는 뺀다 ("초안 끝내야 해" → "초안").
- 날짜를 지어내지 않는다. 등록할 때 마감을 말하지 않았으면 clarify로 마감을 묻는다.
- 업무 이름을 알 수 없으면 clarify로 이름을 묻는다.
- item_id는 반드시 open_tasks에 있는 값만 쓴다. 대상 업무가 없거나 여러 개와 맞으면 clarify (여러 개면 candidates에 넣는다).
- 알림 시점을 말하지 않았으면 offset_minutes는 null.
- offset_minutes는 마감 몇 분 전에 알릴지다 ("2시간 전" → 120).
- clarify가 아니면 question은 null.
- pending이 있으면 사용자가 직전 재질문에 답하는 중이다. pending의 원래 요청과 이번 답을 합쳐 최종 action을 정한다.
"""


def _format_now(now: datetime) -> str:
    return f"{now.isoformat(timespec='minutes')} ({WEEKDAYS[now.weekday()]}요일)"


def _build_context(now: datetime, open_tasks: list[Task], pending: dict | None) -> str:
    """맥락을 프롬프트 텍스트로 만든다. 형식: now(요일 포함) / open_tasks JSON / pending JSON(있을 때만)."""
    tasks = [
        {
            "item_id": t.item_id,
            "name": t.name,
            "due_at": t.due_at.isoformat(timespec="minutes"),
            "status": t.status,
        }
        for t in open_tasks[:MAX_TASKS]
    ]
    lines = [
        "[맥락]",
        f"now: {_format_now(now)}",
        f"open_tasks: {json.dumps(tasks, ensure_ascii=False)}",
    ]
    if pending:
        lines.append(f"pending: {json.dumps(pending, ensure_ascii=False, default=str)}")
    return "\n".join(lines)


async def analyze(
    *,
    audio: bytes | None,
    text: str | None,
    now: datetime,
    open_tasks: list[Task],
    pending: dict | None,
) -> Decision:
    """오디오 또는 텍스트를 맥락과 함께 LLM에 한 번 보내고, 응답 JSON을 Decision으로 검증해 돌려준다.

    pending은 db.pending.payload를 그대로 넘긴다. 권장 모양: 직전 Decision.model_dump(mode="json")
    (transcript, action, args, question 포함) — 원래 요청과 되물은 질문을 LLM이 함께 본다.

    LLM 응답이 스키마에 맞지 않으면 Decision.model_validate_json()이 ValidationError를 낸다.
    """
    if audio is None and text is None:
        raise ValueError("audio 또는 text 중 하나는 있어야 한다")

    context = _build_context(now, open_tasks, pending)
    contents: list = []
    if audio is not None:
        contents.append(context + "\n\n[사용자 요청] 아래 오디오")
        contents.append(types.Part.from_bytes(data=audio, mime_type="audio/wav"))
    else:
        contents.append(context + f"\n\n[사용자 요청]\n{text}")

    client = genai.Client(api_key=settings.llm_api_key or None)
    response = await client.aio.models.generate_content(
        model=settings.llm_model or DEFAULT_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )
    return Decision.model_validate_json(response.text)
