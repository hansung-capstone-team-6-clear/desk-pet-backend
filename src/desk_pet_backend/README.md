# src/desk_pet_backend/ — 코드 지도

```text
desk_pet_backend/
├── main.py       앱 진입점: FastAPI 생성, 라우터 등록, /health
├── config.py     환경 변수 (.env)
├── schemas.py    주고받는 데이터 모양 = contracts.md를 코드로 옮긴 것
├── api/          ① 입구 — Pi 요청을 받는다 (Controller 역할)
├── services/     ② 규칙·흐름 — 무엇을 어떤 순서로 할지 (Model의 로직), 업무 목록 캐시
├── eureka.py     ③ 바깥 연결 — Eureka API (실제 / mock)
└── db.py         ③ 바깥 연결 — SQLite (알림 오프셋·재질문 대기)
```

## 호출 방향

```text
api/  →  services/  →  eureka.py, db.py
```

- 화살표 반대 방향으로 부르지 않는다. 예: `eureka.py`가 `services/`를 import하면 안 된다.
- `config.py`, `schemas.py`는 어디서든 import해도 된다.

## 요청 하나가 지나가는 길

음성 요청 (`POST /api/text`, `POST /api/voice`):

```text
api/voice.py → services/tasks.py handle()
                 ├─ services/task_cache.py get_open_tasks()  미완료 업무 목록 (캐시)
                 ├─ db.py       pending 조회         재질문 대기 중인지
                 ├─ services/ai.py analyze()         LLM 1회 → Decision
                 ├─ (정책 검증)                       기본값 60분, item_id 확인, 과거 날짜 거절
                 ├─ eureka.py   create_task() 등     실제 실행 → 캐시 반영
                 └─ 템플릿 문장 → VoiceResponse
```

업무 목록 (`GET /api/tasks`):

```text
api/tasks.py → services/tasks.py list_device_tasks()
                 ├─ services/task_cache.py get_open_tasks()  미완료 업무 목록 (캐시)
                 └─ db.py       get_reminders()      업무별 알림 오프셋
```

마감 알람은 Pi가 이 목록으로 스스로 울린다. 캐시는 서버 시작 시 `main.py`가 `task_cache.run_forever()`로 띄운다. 지금 있는 `services/reminder.py`, `api/ws.py`의 알림 전송, `alerts` 테이블은 삭제 예정이다.

## 왜 이 구조인가

- **MVC를 그대로 쓰지 않는 이유**: 이 서버에는 화면(View)이 없다. 화면은 Pi의 LED·LCD·스피커다.
  그래서 "입구(api) → 규칙(services) → 바깥 연결(eureka.py, db.py)" 3층으로 나눴다.
  응답 JSON의 모양(`schemas.py`)이 View에 가장 가깝다.
- **기능별 폴더(tasks/ 등) 대신 역할별 폴더를 쓴 이유**: 기능이 많지 않아
  "HTTP 코드는 api/, 규칙은 services/"가 찾기 쉽다. 기능이 5개 이상으로 늘면 기능별 폴더를 검토한다.
- **바깥 연결이 파일 2개뿐이라 폴더를 만들지 않았다.** 3개 이상이 되면 폴더로 묶는다.

## 새 코드를 어디에 쓰나

| 하려는 것 | 위치 |
| --- | --- |
| 새 URL 추가 | `api/` 파일 + `main.py`에 라우터 등록 |
| 업무 규칙 추가·변경 | `services/tasks.py` |
| LLM 프롬프트·모델 변경 | `services/ai.py` |
| Eureka 호출 추가 | `eureka.py`의 `EurekaClient`와 `EurekaMock`에 **둘 다** |
| 테이블·컬럼 추가 | `db.py` + knowledge 레포 `contracts.md` § 6 |
| 환경 변수 추가 | `config.py` + `.env.example` |
