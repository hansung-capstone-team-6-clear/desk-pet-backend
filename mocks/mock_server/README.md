# Desk Pet Mock Server

실제 Eureka Process, LLM/MCP를 붙이기 전에 Desk Pet의 핵심 요청/응답 계약을 검증하기 위한 FastAPI Mock Server입니다.

## 포함 기능

- `POST /api/llm/analyze`
  - 사용자 문장을 받아 LLM이 `intent + parameters + required_tool`을 만든 것처럼 응답
  - 별도 경량 intent classifier는 사용하지 않음
- `POST /api/mcp/call`
  - MCP tool 호출 흐름 흉내
- 업무 API
  - `POST /api/tasks`
  - `GET /api/tasks`
  - `POST /api/tasks/{id}/complete`
  - `GET /api/tasks/{id}/status`
  - `PUT /api/tasks/{id}`
- Eureka Mock API
  - `POST /_api_/items/0/start`
  - `GET /_api_/items/0/list?sites=&detail=true`
  - `POST /_api_/items/{itemId}/complete`
  - `POST /_api_/stages/{stageId}/status`
  - `PUT /_api_/items/{itemId}`
  - `GET /_api_/items/{itemId}/status`
- WebSocket
  - `ws://localhost:8000/ws/{user_id}`
  - 마감 임계치 도달 시 `proactive_alert` push
- Debug API
  - 원하는 업무에 대해 즉시 alert 강제 발생
  - mock 데이터 초기화

---

## 1. 실행

```bash
python -m venv .venv
```

### Windows

```bash
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### macOS / Linux

```bash
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

실행 후:

- Swagger: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

---

## 2. 대표 E2E 테스트

### Step 1. 업무 등록

Swagger에서 `POST /api/tasks`

예시:

```json
{
  "request_id": "req-demo-001",
  "user_id": "demo-user",
  "title": "운영체제 과제",
  "description": "6주차 과제 제출",
  "deadline": "2026-09-29T20:00:00+09:00",
  "priority": "high",
  "alert_before_minutes": 60
}
```

응답의 `task.id`를 복사합니다.

### Step 2. 업무 조회

`GET /api/tasks?user_id=demo-user`

### Step 3. WebSocket 연결

브라우저 개발자 콘솔에서:

```javascript
const ws = new WebSocket("ws://127.0.0.1:8000/ws/demo-user");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

연결되면:

```json
{
  "event": "connected",
  "user_id": "demo-user"
}
```

형태의 메시지가 옵니다.

### Step 4. proactive_alert 강제 발생

Swagger에서:

`POST /api/debug/tasks/{task_id}/trigger-alert`

Body:

```json
{}
```

WebSocket 콘솔에:

```json
{
  "event": "proactive_alert",
  "type": "debug",
  "task_id": "...",
  "title": "운영체제 과제",
  "led": {
    "color": "yellow",
    "mode": "blink"
  },
  "lcd": {
    "expression": "urgent",
    "text": "운영체제 과제 마감 임박!"
  },
  "tts": "운영체제 과제 마감 시간이 얼마 남지 않았어요."
}
```

가 들어오면 Backend → Raspberry Pi 능동 알림 계약 테스트 성공입니다.

---

## 3. 실제 자동 마감 감지

업무의:

```json
"alert_before_minutes": 60
```

이면 서버가 주기적으로 확인하다가

`현재 시간 >= deadline - 60분`

이 되는 순간 WebSocket으로 `proactive_alert`를 한 번 보냅니다.

개발 중에는 기본 확인 주기가 5초입니다.

```bash
SCHEDULER_INTERVAL_SECONDS=5 uvicorn app.main:app --reload
```

실제 구조에서는 이 값을 300초(5분)로 두면 기존 설계와 동일하게 테스트할 수 있습니다.

---

## 4. 실제 Eureka로 바꿀 때

현재는 `tasks` 딕셔너리에 업무를 저장합니다.

추후 실제 Eureka API Key를 적용할 때는 Backend의 task service 내부를 다음 호출로 교체하면 됩니다.

- 등록 → `POST /_api_/items/0/start`
- 조회 → `GET /_api_/items/0/list`
- 완료 → `POST /_api_/items/{itemId}/complete`
- 단계 상태 변경 → `POST /_api_/stages/{stageId}/status`
- 연기/변경 → `PUT /_api_/items/{itemId}`
- 상태 확인 → `GET /_api_/items/{itemId}/status`

외부 Eureka가 Push/Webhook을 제공하지 않는 전제에서는 Backend Scheduler가 주기적으로 조회하고 임계치를 감지한 뒤 Raspberry Pi에 WebSocket으로 이벤트를 Push하면 됩니다.

---

## 5. 오류 테스트

### 없는 업무 ID

`GET /api/tasks/not-found/status`

→ `404 TASK_NOT_FOUND`

### 중복 request_id

같은 `request_id`로 업무를 두 번 등록

→ `409 DUPLICATE_REQUEST_ID`

이걸로 중복 변경 방지 계약도 테스트할 수 있습니다.

### 지원하지 않는 MCP operation

`POST /api/mcp/call`에서 잘못된 operation 전달

→ `400 UNSUPPORTED_MCP_OPERATION`

---

## 6. 발표용으로 보여줄 최소 화면

1. Swagger `/docs`
2. `POST /api/tasks` 성공 응답
3. `GET /api/tasks`에서 등록 내용 확인
4. WebSocket 연결 콘솔
5. `trigger-alert` 호출
6. 콘솔에 `proactive_alert` 수신

이 6개 캡처면 Mock Server + 대표 시나리오 E2E 증빙으로 쓰기 좋습니다.
