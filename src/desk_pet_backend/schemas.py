"""레이어 간 데이터 형식.

정본은 knowledge 레포 projects/desk-pet/contracts.md 다. 형식을 바꾸려면 그 문서 PR을 먼저 올린다.
JSON은 camelCase(deviceId), 파이썬 코드에서는 snake_case(device_id)로 쓴다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Contract(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# contracts § 1 — Pi → Backend 요청과 응답

class TextRequest(Contract):
    device_id: str
    text: str


ResponseType = Literal[
    "task_created",
    "task_completed",
    "task_updated",
    "task_list",
    "reminder_set",
    "clarify",
    "no_speech",
    "error",
]


class VoiceResponse(Contract):
    request_id: str
    type: ResponseType
    text: str


# contracts § 2 — Backend → Pi 선제 알림 (WebSocket)

class ProactiveAlert(Contract):
    event: Literal["proactive_alert"] = "proactive_alert"
    item_id: str
    task_name: str
    remaining_minutes: int
    text: str


# contracts § 3 — AI 출력

Action = Literal[
    "create_task",
    "list_tasks",
    "complete_task",
    "update_due",
    "set_reminder",
    "clarify",
]


class DecisionArgs(Contract):
    name: str | None = None
    due_at: datetime | None = None
    offset_minutes: int | None = None
    item_id: str | None = None
    range: Literal["today", "tomorrow", "week", "all"] | None = None
    candidates: list[str] | None = None


class Decision(Contract):
    transcript: str
    action: Action
    args: DecisionArgs
    question: str | None = None


# contracts § 4 — Eureka 업무 (eureka.py가 Eureka 응답을 이 모양으로 바꾼다)

class Task(Contract):
    item_id: str
    name: str
    due_at: datetime
    status: Literal["todo", "doing", "done", "hold"]
