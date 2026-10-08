# Desk Pet Backend

데스크펫의 서버. Raspberry Pi가 보낸 음성·텍스트를 해석해 Eureka Process 업무를 처리하고,
마감이 다가오면 Pi에 먼저 알림을 보낸다.

> 설계 정본은 knowledge 레포 `projects/desk-pet/`다. 구조는 `architecture.md`, 주고받는 형식은 `contracts.md`.
> 형식을 바꾸려면 `contracts.md` PR부터 올린다.

## 이 레포가 하는 일

| 한다 | 하지 않는다 (Hardware 레포가 함) |
| --- | --- |
| `/voice`·`/text` 요청 처리, LLM 1회 호출, Eureka 호출, 마감 알림 판단·전송, SQLite 저장 | 녹음, TTS, 재생, LED·LCD 표시 |

## 폴더 구조

```text
desk-pet-backend/
├── src/desk_pet_backend/   서버 코드 — 코드 지도는 src/desk_pet_backend/README.md
│   ├── main.py             앱 진입점, /health
│   ├── config.py           환경 변수
│   ├── schemas.py          주고받는 데이터 모양 (contracts.md를 코드로)
│   ├── api/                ① 입구: /voice, /text, /ws
│   ├── services/           ② 규칙·흐름: tasks(요청 처리), ai(LLM), reminder(마감 알림)
│   ├── eureka.py           ③ 바깥 연결: Eureka API + 개발용 mock
│   └── db.py               ③ 바깥 연결: SQLite
├── tests/                  자동 테스트, AI 평가셋, Eureka PoC 스크립트 — tests/README.md
├── mocks/                  이전 단계 Mock 서버 (참고용) — mocks/README.md
├── docs/                   이전 설계 문서 (knowledge로 이전 중) — docs/README.md
├── .env.example            환경 변수 목록 (복사해서 .env로)
├── Dockerfile, docker-compose.yml
└── pyproject.toml          의존성, pytest·ruff 설정
```

호출 방향은 한쪽으로만: `api/ → services/ → eureka.py, db.py`. 이유와 규칙은 `src/desk_pet_backend/README.md`.

## 처음 실행하기

준비물: Python 3.11 이상, (선택) Docker Desktop.

```bash
git clone https://github.com/hansung-capstone-team-6-clear/desk-pet-backend.git
cd desk-pet-backend
cp .env.example .env
```

로컬에서 바로 실행:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn desk_pet_backend.main:app --reload
```

Docker로 실행:

```bash
docker compose up --build
```

확인:

1. http://localhost:8000/health → `{"status": "ok"}`
2. http://localhost:8000/docs → API 목록 (`/text`, `/voice`는 아직 501)
3. `pytest` → 전부 통과

`.env`의 기본값은 `EUREKA_MODE=mock`이라 Eureka 키 없이 개발할 수 있다.

## 1차 개발 순서

코드 곳곳의 `TODO(담당, 기한)` 주석이 할 일 목록이다. `grep -rn "TODO(" src`로 모아 볼 수 있다.

| 기한 | 할 일 | 파일 | 담당 |
| --- | --- | --- | --- |
| 10/18 | Eureka 실제 호출 | `eureka.py` `EurekaClient` | backend |
| 10/18 | LLM 호출 + 평가셋 20개 | `services/ai.py`, `tests/eval/` | ai |
| 10/18 | `/text` 요청 처리 | `services/tasks.py`, `api/voice.py` | backend |
| 10/31 | `/voice` 음성 요청 | `api/voice.py`, `services/tasks.py` | backend |
| 11/14 | 마감 알림 | `services/reminder.py`, `api/ws.py`, `main.py` | backend |

이미 동작하는 것: `/health`, 토큰 확인, `WS /ws/{deviceId}` ping/pong, `EurekaMock`, SQLite 테이블 생성, 시간 변환.
Hardware 쪽은 지금 바로 WS 연결 테스트를 할 수 있다.

## 작업 규칙

1. `main`에 직접 push하지 않는다. 브랜치(`feat/…`, `fix/…`) → PR → 팀원 1명 리뷰 → merge.
2. 커밋 메시지 앞에 종류를 붙인다: `feat:` `fix:` `docs:` `test:` `chore:`.
3. PR 전에 `pytest`와 `ruff check .`이 통과해야 한다.
4. `.env`, API 키는 커밋하지 않는다. 새 환경 변수는 `config.py`와 `.env.example`에 같이 추가한다.
5. 파일은 UTF-8(BOM 없음)로 저장한다. BOM이 붙으면 `pyproject.toml`을 읽지 못해 설치가 깨진다 (`.editorconfig`가 막아 준다).
