# Desk Pet 백엔드 인터페이스

현재 백엔드 코드 기준이다. 팀 계약의 정본은 knowledge 레포의 `projects/eureka-desk-pet/contracts.md`다. [백엔드 아키텍처](../architecture/backend_architecture.md)

## Pi 및 평가 클라이언트 → Backend

| 호출 | 입력 | 응답 |
| --- | --- | --- |
| `POST /api/voice` | multipart의 `audio`, 선택 `user_id`·`request_id` | `VoiceResponse` |
| `POST /api/text` | JSON의 `text`, 선택 `user_id`·`request_id` | `VoiceResponse` |
| `GET /health` | 입력 없음 | `{"status":"ok"}`. 앱 응답 확인용 |

두 POST는 `X-Device-Token`이 필요하다. WAV는 16kHz, 모노, 16비트 PCM, 최대 15초 및 1,000,000바이트다. 토큰 오류는 401, 크기·길이 초과는 413, 입력 형식 오류는 422다. `user_id` 생략 시 `default`를 사용한다. `request_id`는 받은 값을 돌려주고 생략 시 생성한다. 업로드·검증 뒤 업무 처리는 7초 제한이고 Pi는 10초를 기다린다. 2026-11-06에 Pi 10초 지연 안내·20초 실패로 바꾸기로 했다 (구현 전, `contracts.md` § 1).

```json
{
  "request_id": "req_example",
  "status": "OK",
  "stt_text": "오늘 업무 알려 줘",
  "type": "task_list",
  "output": {
    "tts": "해당 기간에 미완료 업무가 없어요.",
    "lcd": { "expression": "speaking", "text": "업무 0건" }
  }
}
```

`stt_text`와 `output.lcd`는 null일 수 있다. 구현된 응답은 `OK`와 `ERROR`, 결과 유형은 `task_created`·`task_list`·`task_completed`·`task_updated`·`clarification`·`error`다. `NO_SPEECH`·`STT_ERROR`, `reminder_set`은 스키마 정의 단계다. LED 매핑은 Pi가 담당한다.

## Backend 내부

| 경계 | 주고받는 값 |
| --- | --- |
| `tasks.py → ai.py` | WAV 또는 텍스트, 현재 KST 시각, 미완료 업무 최대 30건, 유효한 `pending` |
| `ai.py → tasks.py` | `Decision`의 `transcript`, `action`, `args`, 선택 `question` |
| `tasks.py → db.py` | 재질문은 `user_id`별 `Decision` JSON을 60초 보관한다. 등록 시 `item_id`와 알림 오프셋을 저장한다. |

`args`의 선택 필드는 `name`, `due_at`, `offset_minutes`, `item_id`, `range`, `candidates`다. `range`는 `today`·`tomorrow`·`week`·`all`이다. 실행하는 action은 `create_task`·`list_tasks`·`complete_task`·`update_due`·`clarify`다. `set_reminder`는 후보에 있으나 요청 처리에서 미지원이다.

## Backend → Eureka

실연동은 `x-api-key`를 사용한다. 업무 데이터는 `item_id`·`name`·`due_at`·`status`로 정규화하며, `dueAt`은 Eureka의 epoch 밀리초 값이다.

| 동작 | HTTP 및 필드 |
| --- | --- |
| 조회 | `GET /_api_/items/0/list?sites&detail=true&limit=100`. 원본 100건 이상이면 오류. `done` 제외 |
| 등록 | `POST /_api_/items/0/start`. `processId`·`name`·`dueAt`·`priority=high` |
| 완료 | `POST /_api_/items/{item_id}/complete` |
| 마감 변경 | `PUT /_api_/items/{item_id}`. `dueAt` |

실연동 클라이언트의 단건 상태 조회는 미구현이다. 지금은 요청마다 목록을 조회한다. 2026-11-06에 서버 시작 시·하루 1회 조회 후 캐시로 바꾸기로 했다 (구현 전, `contracts.md` § 4).

## Pi → Backend 업무 목록 (구현 전)

2026-11-06 결정이다. `GET /api/tasks`에 `X-Device-Token`을 전달하면 미완료 업무의 `item_id`, `name`, `due_at`, `offset_minutes`를 돌려준다. Pi는 이 목록으로 스스로 마감 알람을 울린다. 응답 예시는 `contracts.md` § 2에 있다.

## 삭제 예정: WebSocket

`/ws/hardware/{user_id}`에 `X-Device-Token`을 전달한다. 토큰 오류 시 1008로 닫는다. 연결 시 `{"event":"connected","user_id":"…"}`, 텍스트 `PING`에는 `{"event":"PONG"}`을 반환한다. Pi 자체 알람으로 바뀌어 쓰지 않으며, 구현 때 삭제한다.
