# api/ — 입구

Pi가 보내는 요청이 처음 도착하는 곳이다. MVC로 치면 Controller 자리.

## 파일

| 파일 | 하는 일 | 상태 |
| --- | --- | --- |
| `voice.py` | `POST /api/voice`(음성), `POST /api/text`(텍스트) → `services/tasks.py`의 `handle()` 호출 | 동작함. 음성 실파일·실모델 평가는 별도 |
| `tasks.py` | `GET /api/tasks` → `services/tasks.py`의 `list_device_tasks()`. 목록을 못 받으면 503 | 동작함 |
| `ws.py` | `WS /ws/hardware/{user_id}` 연결 보관, `connected` 전송, 텍스트 `PING` → `{"event": "PONG"}`, `send_alert()` | 이전 알림 설계. Pi 자체 알람으로 바뀌어 **삭제 예정** (2026-11-06) |
| `auth.py` | `X-Device-Token` 헤더 확인 | 동작함 |

주소와 메시지 모양은 knowledge 레포 `projects/eureka-desk-pet/contracts.md` § 1·2를 따른다.

## 규칙

1. **얇게 쓴다.** 입력을 받고 → `services/`를 부르고 → 응답을 돌려준다. 업무 규칙(기본 알림 60분 등)은 여기에 쓰지 않는다.
2. `eureka.py`나 `db.py`를 여기서 직접 부르지 않는다. 항상 `services/`를 거친다.
3. 요청·응답 모양은 `schemas.py`의 모델을 쓴다. 새 필드가 필요하면 knowledge 레포의 `contracts.md`부터 고친다.
4. 새 엔드포인트를 추가하면 `main.py`에 `app.include_router(...)`로 등록한다.

## 손으로 확인하기

```bash
# 토큰 없이 → 401
curl -X POST localhost:8000/api/text -H 'content-type: application/json' \
  -d '{"user_id":"pet-01","text":"오늘 할 일"}'

# 토큰 포함 → 처리 결과 200
curl -X POST localhost:8000/api/text -H 'content-type: application/json' -H 'x-device-token: change-me' \
  -d '{"user_id":"pet-01","text":"오늘 할 일 알려줘"}'

# 음성: 16kHz 모노 16비트 PCM WAV, 최대 15초
curl -X POST localhost:8000/api/voice -H 'x-device-token: change-me' \
  -F 'user_id=pet-01' -F 'audio=@speech.wav;type=audio/wav'

# Pi 알람용 업무 목록
curl localhost:8000/api/tasks -H 'x-device-token: change-me'
```

