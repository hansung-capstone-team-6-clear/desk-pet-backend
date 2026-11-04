"""레이어 간 데이터 형식.

정본은 knowledge 레포 projects/eureka-desk-pet/contracts.md 다. 형식을 바꾸려면 그 문서 PR을 먼저 올린다.
필드 이름은 MVP Mock Server와 같은 snake_case다 (2026-10-07 하드웨어 결정).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# contracts § 1 — Pi → Backend 요청과 응답

class TextRequest(BaseModel):
    """request_id를 보내면 응답에 그대로 돌려주고, 생략하면 Backend가 만든다."""

    user_id: str | None = None
    request_id: str | None = None
    text: str


Status = Literal["OK", "NO_SPEECH", "STT_ERROR", "ERROR"]

ResponseType = Literal[
    "task_created",
    "task_completed",
    "task_updated",
    "task_list",
    "reminder_set",
    "clarification",
    "error",
]

Expression = Literal["idle", "listening", "thinking", "speaking", "alert", "surprised", "happy"]


class Lcd(BaseModel):
    expression: Expression
    text: str


class Output(BaseModel):
    """Pi가 재생·표시할 내용. LED 색은 Pi가 정하므로 넣지 않는다."""

    tts: str
    lcd: Lcd | None = None


class VoiceResponse(BaseModel):
    request_id: str
    status: Status
    stt_text: str | None = None
    type: ResponseType
    output: Output


# contracts § 2 — Backend → Pi 선제 알림 (WebSocket)

class ProactiveAlert(BaseModel):
    event: Literal["proactive_alert"] = "proactive_alert"
    type: Literal["deadline_approaching"] = "deadline_approaching"
    user_id: str
    request_id: str  # alert_<item_id>_<due_at epoch ms>
    output: Output


# contracts § 3 — AI 출력 (Backend 내부)

Action = Literal[
    "create_task",
    "list_tasks",
    "complete_task",
    "update_due",
    "set_reminder",
    "clarify",
]


class DecisionArgs(BaseModel):
    name: str | None = None
    due_at: datetime | None = None
    offset_minutes: int | None = None
    item_id: str | None = None
    range: Literal["today", "tomorrow", "week", "all"] | None = None
    candidates: list[str] | None = None


class Decision(BaseModel):
    transcript: str
    action: Action
    args: DecisionArgs
    question: str | None = None


# contracts § 4 — Eureka 업무 (eureka.py가 Eureka 응답을 이 모양으로 바꾼다)

class Task(BaseModel):
    item_id: str
    name: str
    due_at: datetime
    status: Literal["todo", "doing", "done", "hold"]
