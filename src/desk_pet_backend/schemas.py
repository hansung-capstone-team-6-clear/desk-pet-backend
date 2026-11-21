"""레이어 간 데이터 형식.

정본은 knowledge 레포 projects/eureka-desk-pet/contracts.md 다. 형식을 바꾸려면 그 문서 PR을 먼저 올린다.
필드 이름은 MVP Mock Server와 같은 snake_case다 (2026-10-07 하드웨어 결정).
"""

from datetime import date, datetime
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
    "task_deleted",
    "task_delete_cancelled",
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


# contracts § 2 — Pi → Backend 업무 목록 (Pi 자체 마감 알람)

class DeviceTask(BaseModel):
    item_id: str
    name: str
    due_at: datetime
    offset_minutes: int
    reminder_enabled: bool  # false면 Pi는 이 업무의 알림을 울리지 않는다


class DeviceTaskList(BaseModel):
    tasks: list[DeviceTask]


# 이전 설계 — Backend → Pi 선제 알림 (WebSocket), 삭제 예정

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
    "delete_task",
    "set_reminder",
    "clarify",
    "unsupported",  # 업무 관리와 관계없는 말. 안내 문장은 Backend 템플릿 (tasks.py 처리는 Backend 담당)
]


class DecisionArgs(BaseModel):
    name: str | None = None
    due_at: datetime | None = None
    offset_minutes: int | None = None
    reminder_enabled: bool | None = None  # 알림 켜기(true)·끄기(false). None이면 말하지 않음 (create_task·set_reminder)
    item_id: str | None = None
    # list_tasks 조회 범위. KST 날짜, 양 끝 포함. AI가 now·calendar를 보고 계산한다. 둘 다 없으면 전체.
    date_from: date | None = None
    date_to: date | None = None
    candidates: list[str] | None = None
    confirm: Literal["yes", "no"] | None = None  # 삭제 확인 질문에 대한 답 (AI 판단)


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
