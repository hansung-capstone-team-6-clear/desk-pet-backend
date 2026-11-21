# Desk Pet 백엔드 인터페이스

현재 백엔드 코드 기준이다. 팀 계약의 정본은 knowledge 레포의 `projects/eureka-desk-pet/contracts.md`다. [백엔드 아키텍처](../architecture/backend_architecture.md)

## Pi 및 평가 클라이언트 → Backend

| 호출 | 입력 | 응답 |
| --- | --- | --- |
| `POST /api/voice` | multipart의 `audio`, 선택 `user_id`·`request_id` | `VoiceResponse` |
| `POST /api/text` | JSON의 `text`, 선택 `user_id`·`request_id` | `VoiceResponse` |
| `GET /api/tasks` | 입력 없음 | `DeviceTaskList`. Pi 마감 알람용 업무 목록 |
| `GET /health` | 입력 없음 | `{"status":"ok"}`. 앱 응답 확인용 |

`/api/*`는 `X-Device-Token`이 필요하다. WAV는 16kHz, 모노, 16비트 PCM, 최대 15초 및 1,000,000바이트다. 토큰 오류는 401, 크기·길이 초과는 413, 입력 형식 오류는 422다. `user_id` 생략 시 `default`를 사용한다. `request_id`는 받은 값을 돌려주고 생략 시 생성한다. 업로드·검증 뒤 업무 처리는 7초 제한이고 Pi는 10초를 기다린다. 2026-11-06에 Pi 10초 지연 안내·20초 실패로 바꾸기로 했다 (구현 전, `contracts.md` § 1).

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

`stt_text`와 `output.lcd`는 null일 수 있다. 구현된 응답은 `OK`와 `ERROR`, 결과 유형은 `task_created`·`task_list`·`task_completed`·`task_updated`·`task_deleted`·`task_delete_cancelled`·`reminder_set`·`clarification`·`error`다. `NO_SPEECH`·`STT_ERROR`는 스키마 정의 단계다. LED 매핑은 Pi가 담당한다.

### `GET /api/tasks`

미완료 업무를 마감순으로 돌려준다. Pi는 이 목록으로 스스로 마감 알람을 울리고, 중복 알림 방지와 보낸 알람 기록도 Pi가 맡는다. 언제 울릴지는 Pi가 판단하므로 마감이 지난 업무도 거르지 않는다. `offset_minutes`는 등록 때 저장한 값이고, 없으면 `DEFAULT_REMINDER_MINUTES`(60)다. `reminder_enabled`가 `false`면 사용자가 알림을 끈 업무다. 목록에서 빼지 않으며, Pi는 이 업무의 알람을 울리지 않는다.

```json
{
  "tasks": [
    { "item_id": "1000066", "name": "보고서 작성", "due_at": "2026-11-12T15:00:00+09:00", "offset_minutes": 60, "reminder_enabled": true }
  ]
}
```

목록을 처음 읽기도 전에 Eureka 조회가 실패하거나 SQLite 오류가 나면 503 `{"detail":"업무 목록을 가져오지 못했습니다"}`를 돌려준다. 이때 Pi는 저장해 둔 목록을 계속 쓴다.

## Backend 내부

| 경계 | 주고받는 값 |
| --- | --- |
| `tasks.py → task_cache.py` | 미완료 업무 목록 읽기. 쓰기 성공·실패를 알려 캐시를 고친다. |
| `tasks.py → ai.py` | WAV 또는 텍스트, 현재 KST 시각, 미완료 업무 최대 30건, 유효한 `pending` |
| `ai.py → tasks.py` | `Decision`의 `transcript`, `action`, `args`, 선택 `question` |
| `tasks.py → db.py` | 재질문은 `user_id`별 `Decision` JSON을 60초 보관한다. 등록·알림 변경 시 `item_id`별 알림 오프셋과 켜짐 여부(`enabled`)를 저장하고, 삭제 시 지운다. 알림을 꺼도 오프셋은 남겨 다시 켤 때 쓴다. |

`args`의 선택 필드는 `name`, `due_at`, `offset_minutes`, `reminder_enabled`, `item_id`, `date_from`, `date_to`, `candidates`, `confirm`이다. `date_from`·`date_to`는 `list_tasks`의 마감 날짜 범위(YYYY-MM-DD, KST, 양 끝 포함)이며 AI가 계산한다. 둘 다 없으면 전체다. `reminder_enabled`는 `create_task`·`set_reminder`에서 알림 켜기(`true`)·끄기(`false`)를 뜻한다. 실행하는 action은 `create_task`·`list_tasks`·`complete_task`·`update_due`·`delete_task`·`set_reminder`·`clarify`다.

`delete_task`는 바로 지우지 않고 확인 질문을 한다. 60초 안에 온 답의 동의 여부는 AI가 `confirm`(`yes`·`no`·`null`)으로 판단하고, 백엔드는 그 답이 같은 업무에 대한 것인지만 확인한다. `yes`면 지우고, `no`면 취소하고, `null`이면 다시 묻는다 (`contracts.md` § 3).

`unsupported`는 업무 관리와 관계없는 발화에 대한 AI 판단이다. Backend는 `status: OK`, `type: clarification`과 고정된 업무 기능 안내를 반환하고 `stt_text`는 유지한다. 새 재질문 대기를 저장하지 않으며, 기존 재질문·삭제 확인 대기는 해제한다. 업무와 알림 설정은 변경하지 않는다. `clarification`은 재질문뿐 아니라 이 기능 안내에도 사용한다.

## Backend → Eureka

실연동은 `x-api-key`를 사용한다. 업무 데이터는 `item_id`·`name`·`due_at`·`status`로 정규화하며, `dueAt`은 Eureka의 epoch 밀리초 값이다.

| 동작 | HTTP 및 필드 |
| --- | --- |
| 조회 | `GET /_api_/items/0/list?sites&detail=true&limit=100`. 원본 100건 이상이면 오류. `done` 제외 |
| 등록 | `POST /_api_/items/0/start`. `processId`·`name`·`dueAt`·`priority=high` |
| 완료 | `POST /_api_/items/{item_id}/complete` |
| 마감 변경 | `PUT /_api_/items/{item_id}`. `dueAt` |
| 삭제 | `DELETE /_api_/items/{item_id}`. 응답 `{"id": "<item_id>"}` |

목록은 요청마다 읽지 않고 `services/task_cache.py`가 캐시한다. 서버 시작 시와 하루 1회 읽고, 쓰기 성공 시 캐시를 바로 고친 뒤 약 10초 뒤 다시 읽는다. 쓰기 실패 시 즉시 다시 읽고, 다시 읽기가 실패하면 기존 캐시를 쓴다. 규칙은 `contracts.md` § 4다. 실연동 클라이언트의 단건 상태 조회는 미구현이다.

## 이전 설계: WebSocket (Pi 전환 후 삭제)

`/ws/hardware/{user_id}`에 `X-Device-Token`을 전달한다. 토큰 오류 시 1008로 닫는다. 연결 시 `{"event":"connected","user_id":"…"}`, 텍스트 `PING`에는 `{"event":"PONG"}`을 반환한다. 알림 전송 코드는 삭제했다. Hardware 앱이 아직 연결하므로 Pi가 `GET /api/tasks`로 바꾼 뒤 엔드포인트도 삭제한다.
