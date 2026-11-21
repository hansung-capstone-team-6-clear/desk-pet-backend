"""LLM 1회 호출 → Decision (contracts § 3). 담당: ai.

모델을 바꿀 때는 이 파일과 config의 llm_* 값만 바뀌어야 한다.
1차 모델은 Gemini Flash-Lite (knowledge 레포 projects/eureka-desk-pet/ai/README.md).
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from desk_pet_backend.config import settings
from desk_pet_backend.schemas import Decision, Task

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash-lite"
RETRY_DELAY_SECONDS = 0.5
MAX_TASKS = 30
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

SYSTEM_PROMPT = """
너는 데스크펫 업무 비서다. 사용자 요청과 맥락을 보고 아래 JSON 객체 하나만 출력한다. 배열로 감싸지 않는다.

{"transcript": str, "action": str, "args": {...}, "question": str | null}

- transcript: 이번 [사용자 요청]에서 사용자가 말한(입력한) 문장만 그대로 적는다. 오디오면 받아 적은 문장.
  pending.transcript나 이전 발화를 앞뒤에 붙이거나 합치지 않는다. 이번에 짧게 답했으면 짧은 답만 적는다.
  예: pending.transcript가 "보고서 작성 추가해줘"이고 이번에 "내일 오후 3시까지"라고 했으면 transcript는 "내일 오후 3시까지".
  예: 삭제 확인 질문에 "응"이라고 했으면 transcript는 "응".
- args에는 해당 action이 쓰는 키만 넣는다. 나머지 키는 생략한다. 단, clarify는 지금까지 확정된 name·due_at·offset_minutes도 함께 넣는다.

[action과 args]
1. create_task: name, due_at, offset_minutes, reminder_enabled
2. list_tasks: date_from, date_to — 조회할 마감 날짜 범위 "YYYY-MM-DD" (양 끝 포함). now와 this_week를 보고 계산한다.
   "오늘" → 둘 다 오늘, "내일" → 둘 다 내일, "이번 주" → this_week의 월요일~일요일, "다음 주" → 그다음 월요일~일요일.
   기간을 말하지 않으면 둘 다 넣지 않는다 (전체 조회).
3. complete_task: item_id
4. update_due: item_id, due_at (마감 변경·연기·당기기)
5. set_reminder: item_id, offset_minutes, reminder_enabled (알림 설정·변경·끄기·켜기)
   - 알림을 끄거나 없애 달라면 reminder_enabled false, 다시 켜 달라면 true. 이때 offset_minutes는 말했을 때만 넣는다.
   - 알림 시점("30분 전")만 바꾸면 offset_minutes만 넣는다.
   - create_task에서도 "알림 없이", "알림은 필요 없어"라고 하면 reminder_enabled false. 말하지 않으면 넣지 않는다.
6. delete_task: item_id, confirm (업무 삭제·지우기·없애기. 실제 삭제 전에 Backend가 확인 질문을 한다.
   confirm은 확인 질문에 답할 때만 "yes" | "no" | null로 쓰고, 처음 삭제를 요청할 때는 넣지 않는다)
7. clarify: candidates (후보 item_id 배열, 선택) + question 필수 (사용자에게 되물을 짧은 한국어 문장)
8. unsupported: args 없음 — 업무 관리(등록·조회·완료·마감 변경·삭제·알림)와 관계없는 말. 안내 문장은 Backend가 만든다.
   - 잡담·추천·날씨·검색·인사·감사 ("저녁메뉴 추천해줘", "오늘 날씨 어때?", "심심해", "고마워")
   - 특정 업무를 가리키지 않는 타이머·시간 알림 ("5분 후에 알려줘", "10분 뒤에 깨워줘")
   - 업무 관리를 뜻하지만 대상이나 정보가 빠진 말은 unsupported가 아니라 clarify다.
     open_tasks가 비어 있어도 마찬가지다 ("그거 수정해줘", "하나 추가해줘", "그거 지워줘" → clarify).

[규칙]
- due_at은 +09:00이 붙은 ISO 8601 (예: 2026-10-09T15:00:00+09:00). now를 기준으로 계산한다. 시각 없이 날짜만 말하면 23:59.
  사용자가 날짜나 시각을 말했으면 create_task·update_due의 due_at을 null로 두지 말고 반드시 계산해 채운다.
- 기존 업무의 날짜만 바꾸면("모레로", "다음 주 수요일로") 원래 due_at의 시각을 그대로 둔다.
- name은 업무를 나타내는 명사구만 쓴다. "끝내기", "해야 해" 같은 동사·어미는 뺀다 ("초안 끝내야 해" → "초안").
  "제출", "검토", "작성"처럼 일의 내용을 나타내는 명사는 남긴다 ("과제 제출해야 해" → "과제 제출").
  "내일 오후 3시까지"처럼 날짜·시간만 있는 말은 name이 될 수 없다.
  name 끝에 붙은 "업무", "일", "할 일", "태스크"는 빼고 일의 내용만 남긴다 ("테스트 업무 등록해줘" → "테스트").
  이런 말만 있고 일의 내용이 없으면("할 일 하나 추가해줘") name을 정할 수 없으니 clarify로 이름을 묻는다.
  새 업무의 name은 사용자가 실제로 말한 단어에서만 가져온다. open_tasks의 이름을 빌려 쓰지 않는다.
- open_tasks에 있는 업무의 마감을 바꾸는 말은 등록이 아니라 update_due다. "바꿔", "미뤄", "당겨", "옮겨", "변경"처럼
  바꾸는 말이 있으면 업무 이름이 문장에 그대로 나와도 create_task를 내지 않는다 ("보고서 검토를 금요일로 미뤄줘" → update_due).
- 업무를 지우거나 취소하는 말은 delete_task다. "완료했어", "끝냈어"는 삭제가 아니라 complete_task다.
- 날짜를 지어내지 않는다. 등록할 때 마감을 말하지 않았거나 "다음 달", "언젠가"처럼 날짜를 하나로 정할 수 없으면 clarify로 마감을 묻는다.
- 업무 이름을 알 수 없으면 clarify로 이름을 묻는다. create_task는 name과 due_at이 모두 정해졌을 때만 내보낸다.
- "내일 해야 할 일 있어", "오늘 뭐 있지"처럼 할 일이 있는지 묻는 말은 등록이 아니라 list_tasks다.
- item_id는 반드시 open_tasks에 있는 값만 쓴다. 대상 업무가 없거나 여러 개와 맞으면 clarify (여러 개면 candidates에 넣는다).
- 알림 시점을 말하지 않았으면 offset_minutes는 null.
- offset_minutes는 마감 몇 분 전에 알릴지다 ("2시간 전" → 120).
- clarify가 아니면 question은 null. unsupported일 때도 question은 null이고, 기능 밖 질문에 직접 답하지 않는다.
- pending이 있으면 사용자가 직전 재질문에 답하는 중이다. pending의 원래 요청과 이번 답을 합쳐 최종 action을 정한다.
  - pending.transcript와 pending.question으로 원래 하려던 일(등록, 완료 등)을 파악하고 그 일을 바꾸지 않는다. 완료하려던 요청이 마감 변경이 되면 안 된다.
  - 이번 답에 pending.question이 물은 정보(업무, 날짜 등)가 없으면 추측해서 실행하지 말고 같은 질문을 다시 clarify한다.
  - 이번 답이 질문과 상관없는 새 요청이면 pending을 무시하고 새 요청으로 처리한다.
  - pending.action이 delete_task면 Backend가 "{업무} 업무를 삭제할까요?"라고 확인 질문을 한 상태다. 이번 답으로 confirm을 정한다.
    - 삭제에 동의하면("응", "네", "그래", "좋아", "알겠어요", "지워줘") delete_task, item_id는 pending과 같게, confirm "yes".
    - 거절하면("아니", "취소해", "하지 마", "그냥 둬") delete_task, item_id는 pending과 같게, confirm "no".
    - 동의인지 거절인지 알 수 없으면("잠깐만", "음", "글쎄") delete_task, item_id는 pending과 같게, confirm null.
      확실한 거절이 아니면 no로 정하지 않는다.
    - 확인 질문과 상관없는 새 요청이면 pending을 무시하고 새 요청으로 처리한다. 이때 confirm은 쓰지 않는다.
"""


def _format_now(now: datetime) -> str:
    return f"{now.isoformat(timespec='minutes')} ({WEEKDAYS[now.weekday()]}요일)"


def _format_this_week(now: datetime) -> str:
    """이번 주 월요일~일요일 날짜. AI가 "이번 주"·"다음 주"를 직접 세지 않게 코드가 계산해 준다."""
    monday = now.date() - timedelta(days=now.weekday())
    return f"{monday.isoformat()}(월) ~ {(monday + timedelta(days=6)).isoformat()}(일)"


def _build_context(now: datetime, open_tasks: list[Task], pending: dict | None) -> str:
    """맥락을 프롬프트 텍스트로 만든다.

    형식: now(요일 포함) / this_week(월~일 날짜) / open_tasks JSON / pending JSON(있을 때만).
    """
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
        f"this_week: {_format_this_week(now)}",
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

    재시도는 아래 두 경우에만, 둘을 합쳐 1회 한다. 다시 실패하면 예외를 그대로 올린다 (handle()이 ERROR로 응답).
    - Gemini 503
    - 응답이 깨진 JSON이거나 Decision과 맞지 않을 때 (ValidationError)
    재시도 시간도 handle()의 전체 제한 시간 안에 포함된다.

    응답 스키마(response_json_schema)는 쓰지 않는다. 11/12 평가에서 스키마를 지정하면
    말한 마감(due_at)을 빠뜨리거나 args를 비우는 경우가 늘었다.
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
    request = {
        "model": settings.llm_model or DEFAULT_MODEL,
        "contents": contents,
        "config": types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            temperature=0.1,
        ),
    }
    for attempt in (1, 2):
        try:
            response = await client.aio.models.generate_content(**request)
            return Decision.model_validate_json(response.text)
        except errors.APIError as exc:
            if attempt == 2 or exc.code != 503:
                raise
            log.warning("gemini returned 503; retrying once")
            await asyncio.sleep(RETRY_DELAY_SECONDS)
        except ValidationError:
            if attempt == 2:
                raise
            log.warning("gemini returned invalid decision JSON; retrying once")
    raise AssertionError("unreachable")
