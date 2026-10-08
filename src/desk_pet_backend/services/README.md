# services/ — 규칙과 흐름

"무엇을 어떤 순서로, 어떤 규칙으로 하는가"를 담는 곳이다. MVC로 치면 Model의 로직 부분.

## 파일

| 파일 | 하는 일 | 담당 | 기한 |
| --- | --- | --- | --- |
| `tasks.py` | 요청 하나 처리: 업무 목록 조회 → `ai.analyze()` → 정책 검증 → Eureka 실행 → 응답 문장 | backend | 10/18 (`/text`), 10/31 (`/voice`) |
| `ai.py` | LLM 1회 호출 → `Decision` | ai | 10/18 |
| `reminder.py` | 60초마다 마감 확인 → 알림 전송·기록, 재연결 시 재전송 | backend | 11/14 |

## 규칙

1. **업무 정책은 `tasks.py`에만 쓴다.** 기본 알림 60분, itemId 검증, 과거 날짜 거절, "Eureka 성공 확인 전엔 성공이라고 말하지 않기".
2. **LLM은 `ai.py`에서만 부른다.** 요청 하나에 한 번. 응답 문장을 만들려고 다시 부르지 않는다 (템플릿 사용).
3. Eureka는 `eureka.py`의 `get_eureka()`를 통해서만, 저장은 `db.py`를 통해서만 접근한다. HTTP 요청 코드를 여기에 쓰지 않는다.
4. 시각은 항상 시간대가 붙은 `datetime`(+09:00)으로 다룬다. epoch ms는 `eureka.py` 밖으로 나오지 않는다.

## LLM SDK 추가 (ai 담당)

1차 기본 모델은 Gemini Flash-Lite 무료 등급이다 (knowledge 레포 `projects/desk-pet/ai/README.md`).
모델을 확정하면 `pyproject.toml`의 `dependencies`에 SDK를 추가하고, `.env.example`의 `LLM_MODEL`·`LLM_API_KEY` 설명을 채운다.
SDK 없이 `httpx`(이미 설치됨)로 REST API를 직접 불러도 된다.

## 함수 하나를 구현할 때 순서

1. docstring의 단계 목록을 읽는다.
2. `tests/`에 실패하는 테스트를 하나 쓴다 (예: offsetMinutes가 없으면 60이 들어가는지).
3. 구현해서 테스트를 통과시킨다.
4. `TODO(...)` 주석을 지우고 PR을 올린다.
