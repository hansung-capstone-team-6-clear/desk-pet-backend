# Desk Pet 백엔드 아키텍처

이 문서는 현재 백엔드 코드의 1차 MVP 구현을 설명한다. 코드에 아직 반영하지 않은 결정은 § 남은 결정에 따로 적는다. 시스템 설계와 레이어 간 계약은 knowledge 레포의 `projects/eureka-desk-pet/architecture.md`와 `contracts.md`에서 관리한다. [인터페이스 명세](../interfaces/backend_interfaces.md) · [레이어 도식](backend_architecture.html)

## 레이어와 책임

| 경계 | 구현과 역할 |
| --- | --- |
| Raspberry Pi | PTT로 녹음하고 WAV를 전송한다. 응답을 TTS로 재생하고 LCD·LED에 표시한다. `GET /api/tasks`로 받은 목록으로 마감 알람을 스스로 울린다. |
| FastAPI | `api/voice.py`에서 디바이스 토큰과 입력을 검증한 뒤 `tasks.handle()`을 호출한다. `api/tasks.py`는 `GET /api/tasks`, `main.py`는 `GET /health`를 제공한다. |
| 업무 처리 | `services/tasks.py`가 업무와 재질문 맥락을 읽고 AI 결과를 검증한다. 등록·조회·수정·완료·삭제·재질문과 응답 문구를 처리한다. |
| 업무 목록 캐시 | `services/task_cache.py`가 Eureka 미완료 업무 목록을 메모리에 들고 있다. 요청 처리와 `GET /api/tasks`가 같은 캐시를 읽는다. |
| 요청 해석 | `services/ai.py`가 Gemini에 오디오 또는 텍스트와 맥락을 보내 구조화된 `Decision`을 한 번 받는다. |
| 외부 연동 | `eureka.py`가 실연동 HTTP와 메모리 모의 구현을 선택한다. 실연동에서는 Eureka가 업무 원본이다. |
| 저장 | `db.py`가 SQLite에 재질문 상태와 알림 오프셋을 저장한다. |

## 요청 흐름

1. Pi는 `POST /api/voice`로 WAV를 보낸다. 개발·평가에서는 `POST /api/text`로 같은 업무 처리 흐름을 호출한다.
2. 백엔드는 토큰과 입력을 확인한다. 업로드·검증 뒤 시작되는 업무 처리에는 7초 제한이 있다.
3. `tasks.py`가 캐시에서 미완료 업무를, SQLite에서 `pending`을 읽는다. Gemini에는 업무를 최대 30건 전달한다.
4. Gemini의 `Decision`을 검증해 등록·조회·수정·완료·삭제·재질문을 처리한다. 삭제는 확인 질문 뒤 긍정 답이 와야 실행한다. 등록은 Eureka에 쓴 뒤 SQLite에 알림 오프셋을 저장한다. 재질문은 사용자별 `Decision`을 60초 보관한다.
5. Eureka 쓰기가 성공하면 캐시를 바로 고치고, 실패하면 캐시를 즉시 다시 읽는다.
6. `VoiceResponse`를 HTTP로 반환한다. 일반 문구는 템플릿으로 만들고, 재질문에는 질문 문구를 담는다.

## 업무 목록 캐시

규칙의 정본은 knowledge 레포 `contracts.md` § 4다.

| 시점 | 동작 |
| --- | --- |
| 서버 시작, 이후 하루 1회 | Eureka 목록을 읽어 캐시를 채운다 (`TASK_CACHE_SECONDS`, 기본 86400). |
| 쓰기 성공 | 그 업무만 캐시에서 고치고, 약 10초 뒤 한 번 다시 읽는다 (`TASK_REFRESH_DELAY_SECONDS`). 여러 번 쓰면 마지막 쓰기 기준으로 한 번만 읽는다. |
| 쓰기 실패 | 즉시 다시 읽는다. |
| 다시 읽기 실패 | 기존 캐시를 그대로 쓴다. 캐시가 아직 없으면 오류가 난다. |

다시 읽는 도중에 쓰기가 일어나면 그 읽기 결과는 버린다. 쓰기가 덮어쓰이지 않게 하기 위해서다. `TASK_CACHE_SECONDS=0`이면 캐시를 쓰지 않고 요청마다 읽는다. 2026-11-11 서버 시험에서 요청당 목록 조회 시간은 약 1.3초에서 0초가 됐다.

Eureka 웹에서 바꾼 내용은 캐시 때문에 최대 하루 늦게 반영될 수 있다. 팀 전체 리뷰 항목이다.

## 남은 결정 (2026-11-06, 구현 전)

| 변경 | 내용 |
| --- | --- |
| 시간 제한 | 처리 7초 → Pi 10초 지연 안내·20초 실패에 맞춰 조정. LLM 재시도 1회. Pi 변경과 함께 배포한다 (`contracts.md` § 1). |

## 이전 알림 코드

마감 알람은 Pi가 `GET /api/tasks`로 직접 울리기로 해서(2026-11-06) Backend 선제 알림 코드는 삭제했다: `services/reminder.py`, `api/ws.py`의 `send_alert()`, `alerts` 테이블, `POLL_INTERVAL_SECONDS`, `EurekaClient.get_status()`. 이미 만들어진 DB 파일의 `alerts` 테이블은 남아 있어도 쓰지 않는다.

`api/ws.py`에는 토큰 검사, `connected`, PING/PONG만 남겼다. Hardware 앱(`app/backend_client.py` `listen()`)이 아직 연결하므로, Pi가 바꾼 뒤 엔드포인트를 삭제한다. `schemas.ProactiveAlert`도 쓰지 않지만 공용 파일이라 따로 정리한다.

## 구현상 제약

- 실연동 목록은 원본 100건 이상이면 완료 필터 전에 오류가 난다. 페이지 처리 방식은 확인이 필요하다.
- `request_id`는 추적용이다. Eureka 등록 뒤 오류가 나면 재요청으로 중복 등록될 수 있다.
- `set_reminder`와 `NO_SPEECH`·`STT_ERROR`는 스키마에 정의돼 있지만 해당 처리 분기는 없다.
- 위 내용은 코드 기준이다. 배포 서버와 실제 Pi·Gemini·Eureka 연동 상태는 별도로 검증한다.
