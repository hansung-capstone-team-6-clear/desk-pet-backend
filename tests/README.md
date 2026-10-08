# tests/

| 위치 | 무엇 | 실행 |
| --- | --- | --- |
| `test_*.py` | 자동 테스트. PR 전에 전부 통과해야 한다 | `pytest` |
| `eval/` | AI 평가셋 — 발화 20개로 정확도·지연 측정 | `eval/README.md` |
| `integration/Eureka_Process_API_Test/` | 실제 Eureka에 직접 호출해 보는 PoC 스크립트 (pytest가 아님, 실제 키 필요) | `python tests/integration/Eureka_Process_API_Test/eureka_session.py` |

## 실행

```bash
pip install -e ".[dev]"   # 처음 한 번
pytest                    # 전체
pytest tests/test_smoke.py -k health   # 하나만
```

## 테스트 쓰는 법

1. 파일 이름은 `test_<대상>.py`, 함수 이름은 `test_<무엇을_확인하는지>`.
2. 실제 Eureka·LLM을 부르지 않는다. Eureka는 `EurekaMock`, LLM은 `monkeypatch`로 가짜 `Decision`을 돌려주게 한다.
3. `async def` 테스트도 그냥 쓰면 된다 (`pyproject.toml`에 `asyncio_mode = "auto"`).
4. 정책(기본 알림 60분, 과거 날짜 거절 등)마다 테스트를 하나씩 둔다. 정책이 깨지면 바로 알 수 있다.

## PoC 스크립트 (integration/)

2026-09 Eureka 연동 PoC에 쓴 스크립트다. 레포 루트의 `.env`에 `EUREKA_API_KEY`가 있어야 하고, 실제 운영 Eureka에 업무를 만들고 지운다.
`eureka.py`의 `EurekaClient`를 구현할 때 요청·응답 모양을 보는 참고용으로 쓴다.
