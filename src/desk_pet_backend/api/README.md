# api/ — 입구

Pi가 보내는 요청이 처음 도착하는 곳이다. MVC로 치면 Controller 자리.

## 파일

| 파일 | 하는 일 | 상태 |
| --- | --- | --- |
| `voice.py` | `POST /api/voice`(음성), `POST /api/text`(텍스트) → `services/tasks.py`의 `handle()` 호출 | TODO — 지금은 501 |
| `ws.py` | `WS /ws/hardware/{user_id}` 연결 보관, `connected` 전송, 텍스트 `PING` → `{"event": "PONG"}`, `send_alert()` | 동작함, 미전달 알림 재전송은 TODO |
| `auth.py` | `X-Device-Token` 헤더 확인 | 동작함 |

주소와 메시지 모양은 2026-10-07 하드웨어 결정(MVP Mock Server 형식)을 따른다 — knowledge 레포 `projects/eureka-desk-pet/contracts.md` § 1·2.

## 규칙

1. **얇게 쓴다.** 입력을 받고 → `services/`를 부르고 → 응답을 돌려준다. 업무 규칙(기본 알림 60분 등)은 여기에 쓰지 않는다.
2. `eureka.py`나 `db.py`를 여기서 직접 부르지 않는다. 항상 `services/`를 거친다.
3. 요청·응답 모양은 `schemas.py`의 모델을 쓴다. 새 필드가 필요하면 knowledge 레포의 `contracts.md`부터 고친다.
4. 새 엔드포인트를 추가하면 `main.py`에 `app.include_router(...)`로 등록한다.

## 손으로 확인하기

```bash
# 토큰 없이 → 401
curl -X POST localhost:8000/api/text -H 'content-type: application/json' \
  -d '{"user_id":"pet-01","request_id":"req_test_1","text":"오늘 할 일"}'

# 토큰 포함 → 지금은 501, 구현 후 200
curl -X POST localhost:8000/api/text -H 'content-type: application/json' -H 'x-device-token: change-me' \
  -d '{"user_id":"pet-01","request_id":"req_test_2","text":"오늘 할 일 알려줘"}'
```

WebSocket은 `http://localhost:8000/docs`에 나오지 않는다. Hardware 레포 `app/`의 클라이언트나 `websocat` 같은 도구로 확인한다.
