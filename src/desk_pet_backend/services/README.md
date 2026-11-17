# services/ — 규칙과 흐름

"무엇을 어떤 순서로, 어떤 규칙으로 하는가"를 담는 곳이다. MVC로 치면 Model의 로직 부분.

## 파일

| 파일 | 하는 일 | 담당 | 기한 |
| --- | --- | --- | --- |
| `tasks.py` | 요청 하나 처리: 업무 목록 읽기 → `ai.analyze()` → 정책 검증 → Eureka 실행 → 캐시 반영 → 응답 문장. `GET /api/tasks` 목록도 만든다 | backend | 10/18 (`/api/text`), 10/31 (`/api/voice`) |
| `task_cache.py` | 미완료 업무 목록 캐시. 서버 시작 시·하루 1회 읽고, 쓰기 성공 시 고친 뒤 약 10초 뒤 다시 읽는다 (`contracts.md` § 4) | backend | - |
| `ai.py` | LLM 1회 호출 → `Decision` | ai | 10/18 |

## 규칙

1. **업무 정책은 `tasks.py`에만 쓴다.** 기본 알림 60분, item_id 검증, 과거 날짜 거절, "Eureka 성공 확인 전엔 성공이라고 말하지 않기".
2. **LLM은 `ai.py`에서만 부른다.** 요청 하나에 한 번. 응답 문장을 만들려고 다시 부르지 않는다 (템플릿 사용).
3. Eureka는 `eureka.py`의 `get_eureka()`를 통해서만, 저장은 `db.py`를 통해서만 접근한다. HTTP 요청 코드를 여기에 쓰지 않는다.
   업무 목록은 `task_cache.get_open_tasks()`로 읽고, Eureka에 쓴 뒤에는 `task_cache.on_*()`로 캐시에 알린다.
4. 시각은 항상 시간대가 붙은 `datetime`(+09:00)으로 다룬다. epoch ms는 `eureka.py` 밖으로 나오지 않는다.

## LLM SDK 추가 (ai 담당)

1차 모델은 Gemini Flash-Lite다 (`ai.py`의 `DEFAULT_MODEL`, knowledge 레포 `projects/eureka-desk-pet/ai/README.md`).
다른 모델로 바꾸면 `pyproject.toml`의 `dependencies`와 `.env.example`의 `LLM_MODEL`·`LLM_API_KEY` 설명을 함께 고친다.

## 함수 하나를 구현할 때 순서

1. docstring의 단계 목록을 읽는다.
2. `tests/`에 실패하는 테스트를 하나 쓴다 (예: offsetMinutes가 없으면 60이 들어가는지).
3. 구현해서 테스트를 통과시킨다.
4. `TODO(...)` 주석을 지우고 PR을 올린다.
