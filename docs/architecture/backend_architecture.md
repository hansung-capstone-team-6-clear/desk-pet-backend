# Desk Pet 백엔드 아키텍처

이 문서는 현재 백엔드 코드의 1차 MVP 구현을 설명한다. 시스템 설계와 레이어 간 계약은 knowledge 레포의 `projects/eureka-desk-pet/architecture.md`와 `contracts.md`에서 관리한다. [인터페이스 명세](../interfaces/backend_interfaces.md) · [레이어 도식](backend_architecture.html)

## 레이어와 책임

| 경계 | 구현과 역할 |
| --- | --- |
| Raspberry Pi | PTT로 녹음하고 WAV를 전송한다. 응답을 TTS로 재생하고 LCD·LED에 표시한다. |
| FastAPI | `api/voice.py`에서 디바이스 토큰과 입력을 검증한 뒤 `tasks.handle()`을 호출한다. `main.py`는 `GET /health`를 제공한다. |
| 업무 처리 | `services/tasks.py`가 업무와 재질문 맥락을 읽고 AI 결과를 검증한다. 등록·조회·완료·재질문과 응답 문구를 처리한다. |
| 요청 해석 | `services/ai.py`가 Gemini에 오디오 또는 텍스트와 맥락을 보내 구조화된 `Decision`을 한 번 받는다. |
| 외부 연동 | `eureka.py`가 실연동 HTTP와 메모리 모의 구현을 선택한다. 실연동에서는 Eureka가 업무 원본이다. |
| 저장 | `db.py`가 SQLite에 재질문 상태와 알림 오프셋을 저장한다. |

## 요청 흐름

1. Pi는 `POST /api/voice`로 WAV를 보낸다. 개발·평가에서는 `POST /api/text`로 같은 업무 처리 흐름을 호출한다.
2. 백엔드는 토큰과 입력을 확인한다. 업로드·검증 뒤 시작되는 업무 처리에는 7초 제한이 있다.
3. `tasks.py`가 Eureka 미완료 업무와 SQLite의 `pending`을 조회한다. Gemini에는 업무를 최대 30건 전달한다.
4. Gemini의 `Decision`을 검증해 등록·조회·완료·재질문을 처리한다. 등록은 Eureka에 쓴 뒤 SQLite에 알림 오프셋을 저장한다. 재질문은 사용자별 `Decision`을 60초 보관한다.
5. `VoiceResponse`를 HTTP로 반환한다. 일반 문구는 템플릿으로 만들고, 재질문에는 질문 문구를 담는다.

## 2차 선제 알림

`api/ws.py`에는 토큰 검사, 연결 관리, `connected`, PING/PONG, `send_alert()`가 있다. 일반 업무 응답은 HTTP로 반환한다. 선제 알림은 1차에서 제외했다. `reminder.py`의 알림 판단·재전송은 미구현이고, 앱 시작 시 폴러를 연결하지 않았다. `alerts`는 테이블 정의 단계다.

## 구현상 제약

- 실연동 목록은 원본 100건 이상이면 완료 필터 전에 오류가 난다. 페이지 처리 방식은 확인이 필요하다.
- `request_id`는 추적용이다. Eureka 등록 뒤 오류가 나면 재요청으로 중복 등록될 수 있다.
- `set_reminder`와 `NO_SPEECH`·`STT_ERROR`는 스키마에 정의돼 있지만 해당 처리 분기는 없다.
- 위 내용은 코드 기준이다. 배포 서버와 실제 Pi·Gemini·Eureka 연동 상태는 별도로 검증한다.
