# Desk Pet Backend Interface Contracts

## 1. Overview

Desk Pet Backend의 API, 내부 서비스, Port, Adapter, Eureka Process 연동 및 WebSocket 메시지 계약을 정의한다.

본 문서는 `backend_architecture.html`의 서버 처리 흐름을 기준으로 한다.

### Architecture Principles

- Raspberry Pi는 WAV 음성을 HTTP로 업로드한다.
- STT 및 Lightweight Intent Model은 서버에서 실행된다.
- 복잡한 요청은 LLM Service가 처리한다.
- LLM은 필요할 경우 MCP Tool을 선택한다.
- MCP 결과는 Orchestrator가 검증한다.
- 업무 변경은 반드시 Application Service를 거친다.
- Eureka Process는 업무 데이터의 Source of Truth이다.
- 일반 음성 응답은 HTTP로 반환한다.
- 서버에서 시작하는 알림은 WebSocket으로 전송한다.
- TTS 음성 합성 및 LED/LCD 하드웨어 제어는 Raspberry Pi가 담당한다.

---

## 2. Voice Processing Interfaces

### 2.1 VoiceAPI

| Field | Value |
|---|---|
| Method | POST |
| Endpoint | `/api/voice` |
| Content-Type | multipart/form-data |
| Caller | Raspberry Pi |
| Handler | Backend VoiceAPI |

Input:
- audio: WAV, 16kHz, mono, 16bit
- user_id: string (선택, 생략하면 기본 사용자)
- request_id: string (선택, 보내면 응답에 그대로 돌려주고 생략하면 Backend가 만든다)

Processing:

1. Raspberry Pi가 WAV 파일을 전송한다.
2. Backend VoiceAPI가 요청을 수신한다.
3. Server STT가 음성을 텍스트로 변환한다.
4. Lightweight Intent Model이 사용자 의도를 분석한다.
5. 필요할 경우 LLM Service 및 MCP Client를 호출한다.
6. Orchestrator가 분석 결과를 검증한다.
7. 해당 Application Service를 호출한다.
8. OutputService가 응답을 생성한다.
9. 동일 HTTP 요청의 응답으로 Raspberry Pi에 반환한다.

Output:

- request_id: string
- status: OK / NO_SPEECH / STT_ERROR / ERROR
- stt_text: string | null
- type: string
- output.tts: string | null
- output.lcd.expression: string | null
- output.lcd.text: string | null

### 2.2 STT Interface

Input:
- WAV audio

Output:
- transcribed_text
- status

Responsibility:
- 서버에서 음성을 텍스트로 변환한다.
- 업무 데이터 변경은 수행하지 않는다.

### 2.3 Lightweight Intent Interface

Input:
- transcribed_text

Output:
- intent
- confidence
- parameters

Responsibility:
- 사용자 의도 분류
- 기본 매개변수 추출
- LLM 처리 필요 여부 판단

### 2.4 AnalyzeUserRequest

Component: LLM Service

Input:
- requestId
- text
- currentDateTime
- conversationContext

Output:
- intent
- parameters
- requiredTool
- requiresClarification
- clarificationQuestion

Rules:
- 복잡하거나 모호한 요청을 분석한다.
- taskName, dueAt, priority, reminderAt을 추출한다.
- 필수 정보가 누락되면 추가 질문을 생성한다.
- LLM은 Eureka Process 데이터를 직접 변경하지 않는다.

### 2.5 CallMCPTool

Flow:

LLM Service
→ MCP Client
→ Intent / LLM Orchestrator
→ Application Service

Input:
- requestId
- toolName
- operation
- parameters

Output:
- requestId
- success
- result
- error

Rules:
- MCP 사용을 유지한다.
- Orchestrator가 결과와 권한을 검증한다.
- 업무 변경은 TaskService를 통해 실행한다.
- 추가 확인이 필요한 요청은 업무 데이터를 변경하지 않는다.

---

## 3. Backend Task APIs

### 3.1 CreateTaskAPI

POST `/api/tasks`

Input:
- request_id
- task_name
- due_at
- priority
- reminder_at (optional)

Output:
- success
- item_id
- status
- message

Flow:

Backend API → TaskService → TaskPort → EurekaTaskAdapter → Eureka Process API

### 3.2 GetTasksAPI

GET `/api/tasks`

Input:
- request_id
- status (optional)
- due_before (optional)
- priority (optional)

Output:
- success
- tasks[]

### 3.3 CompleteTaskAPI

POST `/api/tasks/{item_id}/complete`

Input:
- item_id

Output:
- success
- item_id
- status

### 3.4 GetTaskStatusAPI

GET `/api/tasks/{item_id}/status`

Input:
- item_id

Output:
- success
- item_id
- status
- total_stages
- completed_stages

### 3.5 UpdateTaskAPI

PUT `/api/tasks/{item_id}`

Input:
- item_id
- task_name (optional)
- due_at (optional)
- priority (optional)

Output:
- success
- task

모든 Task API는 TaskService와 TaskPort를 통해 Eureka Process에 접근한다.

---

## 4. Application Services

### TaskService

Operations:
- create_task
- get_tasks
- get_task_status
- complete_task
- update_task

Dependency:
- TaskPort

### ReminderService

Responsibilities:
- 업무 마감 시간 확인
- 완료된 업무 제외
- 사용자 지정 알림 시간 적용
- 기본 마감 1시간 전 알림
- 중복 알림 방지
- OutputService 호출

Dependencies:
- TaskPort
- OutputService

### OutputService

Operation:
- GenerateOutput

Input:
- responseType
- taskName
- result
- remainingTime

Output:
- type
- tts
- lcd.expression
- lcd.text

Rules:
- TTS 텍스트만 생성한다.
- 음성 파일을 직접 생성하지 않는다.
- 물리적 LED 상태를 제어하지 않는다.
- 일반 응답은 HTTP로 반환한다.
- 서버 선제 알림은 OutputPublisherPort로 전달한다.

---

## 5. Ports & Adapters

### 5.1 TaskPort

Direction: Outbound

Methods:
- create_task
- get_tasks
- get_task_status
- complete_task
- update_task

Implementation:
- EurekaTaskAdapter

### 5.2 OutputPublisherPort

Direction: Outbound

Responsibility:
- 서버에서 시작하는 출력 이벤트 전달

Implementation:
- WebSocketAdapter

Events:
- output
- proactive_alert

### 5.3 EurekaTaskAdapter

Responsibility:
- TaskPort 구현
- Eureka Process API 요청
- 외부 응답을 내부 도메인 데이터로 변환
- API 실패 처리

---

## 6. Eureka Process API

| Operation | Method | Endpoint |
|---|---|---|
| CreateTask | POST | `/_api_/items/0/start` |
| GetTasks | GET | `/_api_/items/0/list?sites&detail=true` |
| CompleteTask | POST | `/_api_/items/{itemId}/complete` |
| GetTaskStatus | GET | `/_api_/items/{itemId}/status` |
| UpdateTask | PUT | `/_api_/items/{itemId}` |

Caller:
- EurekaTaskAdapter

Rules:
- 외부 API 직접 호출은 Infrastructure 계층에서 처리한다.
- Application Layer는 Eureka HTTP 구현에 직접 의존하지 않는다.
- 업무 데이터의 최종 상태는 Eureka Process를 기준으로 한다.

---

## 7. Scheduler & Reminder

### 7.1 SchedulerAdapter

Responsibility:
- 주기적으로 ReminderService 실행
- Polling 주기 관리

SchedulerAdapter는 업무 판단 및 알림 메시지 생성을 직접 수행하지 않는다.

### 7.2 Reminder Workflow

SchedulerAdapter
→ ReminderService
→ TaskPort
→ EurekaTaskAdapter
→ Eureka Process API

알림 조건 충족 시:

ReminderService
→ OutputService
→ OutputPublisherPort
→ WebSocketAdapter
→ Raspberry Pi

### 7.3 Reminder Policy

- 완료된 업무는 알림 대상에서 제외한다.
- 사용자 지정 reminderAt이 있으면 해당 시간을 사용한다.
- reminderAt이 없으면 dueAt 1시간 전을 사용한다.
- 동일 업무의 동일 알림을 중복 발송하지 않는다.
- 마감 시간이 변경되면 알림 조건을 재평가한다.
- 알림 상태 저장 방식은 미확정이다.

---

## 8. WebSocket Interface

Endpoint:

`/ws/hardware/{user_id}`

Purpose:
- Raspberry Pi 지속 연결
- 서버 선제 알림
- 비동기 출력 이벤트

### 8.1 proactive_alert

Event:
- proactive_alert

Type:
- deadline_approaching

Payload:
- event
- type
- user_id
- request_id
- output.tts
- output.lcd.expression
- output.lcd.text

### 8.2 output

Event:
- output

Payload:
- event
- type
- user_id
- request_id
- output.tts
- output.lcd.expression
- output.lcd.text

### 8.3 Delivery Rules

- 일반 VoiceAPI 응답은 HTTP를 사용한다.
- 서버에서 시작하는 출력은 WebSocket을 사용한다.
- WebSocket 전송 성공은 하드웨어 출력 완료를 의미하지 않는다.
- Raspberry Pi가 Edge-TTS, LED, LCD 출력을 실행한다.
- WebSocket 메시지에 물리적인 LED 상태 명령은 포함하지 않는다.

---

## 9. Source of Truth

| Data / Responsibility | Owner |
|---|---|
| Task Data | Eureka Process |
| Application Rules | Desk Pet Backend |
| STT / Intent / LLM / MCP Processing | Server-side Processing Components |
| Hardware State | Raspberry Pi |
| Reminder Persistence | TBD |

---

## 10. Unresolved Decisions

1. 사용자 지정 reminderAt의 영속 저장 위치
2. 중복 알림 방지를 위한 상태 저장 구현
3. 네트워크 장애 이후 미전달 알림 재처리 정책
4. 서버 내부 AI 컴포넌트 간 구체적인 통신 인터페이스

본 문서는 기존 Backend Architecture의 인터페이스 기준을 정리한 것이며, 실제 구현 완료를 의미하지 않는다.
