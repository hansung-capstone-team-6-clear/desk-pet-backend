# mocks/ — 이전 단계 Mock 서버 (참고용)

`mock_server/`는 중간 발표 전에 요청·응답 흐름을 시연하려고 만든 독립 FastAPI 서버다. 1차 구현에서는 쓰지 않는다.

| 1차 구현에서 대신 쓰는 것 | 위치 |
| --- | --- |
| 가짜 Eureka | `src/desk_pet_backend/eureka.py`의 `EurekaMock` (`.env`에서 `EUREKA_MODE=mock`) |
| 요청·응답 형식 | `src/desk_pet_backend/schemas.py` (정본: knowledge 레포 `projects/desk-pet/contracts.md`) |

차이가 나는 부분 — 그대로 옮겨 쓰지 않는다:

1. 상태값이 `completed`다. 실제 Eureka는 `todo` / `doing` / `done` / `hold`.
2. LLM 분석·MCP 호출 API(`/api/llm/analyze`, `/api/mcp/call`)와 업무 REST API(`/api/tasks`)가 있다. 1차 구조에는 없다.
3. 알림 payload에 LED 색·LCD 표정이 들어 있다. 1차에서는 Pi가 `type`을 보고 정한다.

1차 구현이 끝나면 이 폴더를 지울지 팀이 정한다. 검사 도구(ruff)는 이 폴더를 건너뛴다.
