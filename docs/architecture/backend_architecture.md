# Desk Pet Backend Architecture

## 1. Overview

Desk Pet Backend는 Raspberry Pi 기반 AI 데스크 펫의 음성 명령, 업무 관리, 마감 알림 및 하드웨어 출력을 조정하는 서버이다.

- FastAPI 기반 HTTP API
- 서버 STT 및 경량 Intent 분석
- LLM / MCP 도구 호출
- Eureka Process 업무 연동
- Polling 기반 마감 알림
- WebSocket 기반 선제적 알림

## 2. Architecture Layers

| Layer | Components | Responsibility |
|---|---|---|
| Device / Hardware | Raspberry Pi, PTT, Microphone, LED, LCD, Speaker | 음성 입력 및 출력 |
| Local Processing | Audio Capture | WAV 녹음 및 전송 |
| Application | STT, Intent, LLM, Orchestrator, Services | 요청 분석 및 업무 처리 |
| Ports | TaskPort, OutputPublisherPort | 외부 시스템 추상화 |
| Infrastructure | EurekaTaskAdapter, WebSocketAdapter, SchedulerAdapter | 외부 연동 및 스케줄링 |
| External | Eureka Process API | 업무 데이터 관리 |

## 3. Voice Processing

User / PTT
→ Raspberry Pi Audio Capture
→ POST /api/voice
→ Server STT
→ Lightweight Intent Model
→ Intent / LLM Orchestrator
→ Application Services

- STT는 서버에서 음성을 텍스트로 변환한다.
- Intent Model은 의도, 신뢰도, 매개변수를 분석한다.
- 복잡하거나 신뢰도가 낮은 요청은 LLM Service로 전달한다.
- LLM Service는 필요할 경우 MCP Client를 사용한다.
- Orchestrator는 AI/MCP 결과를 검증하고 서비스를 호출한다.
- 추가 확인이 필요한 경우 데이터 변경 없이 확인 응답을 반환한다.

## 4. Application Services

### TaskService
- create_task
- get_tasks
- get_task_status
- complete_task
- update_task

### ReminderService
- 마감 시간 확인
- 사용자 지정 알림 시간 적용
- 기본 마감 1시간 전 알림
- 완료된 업무 제외
- 중복 알림 방지

### OutputService
- 사용자 응답 생성
- TTS 텍스트 생성
- LCD 출력 내용 생성
- 출력 명령 전달

## 5. Ports & Adapters

### TaskPort
업무 관리 시스템 접근을 추상화한다.

TaskService
→ TaskPort
→ EurekaTaskAdapter
→ Eureka Process API

Eureka Process는 업무 데이터의 Source of Truth이다.

### OutputPublisherPort
서버에서 시작되는 비동기 하드웨어 출력을 추상화한다.

OutputService
→ OutputPublisherPort
→ WebSocketAdapter
→ Raspberry Pi

## 6. Reminder Workflow

SchedulerAdapter
→ ReminderService
→ TaskPort
→ EurekaTaskAdapter
→ Eureka Process API

알림 조건 충족 시:

ReminderService
→ OutputService
→ OutputPublisherPort
→ WebSocketAdapter
→ Raspberry Pi

### Reminder Policy
- 기본 알림: 마감 60분 전
- 사용자 지정 알림 시간 우선
- 완료된 업무 알림 제외
- 동일 알림 중복 발송 방지
- 마감 시간 변경 시 알림 조건 재평가
- 알림 상태 영속화 방식은 미확정

## 7. Output Communication

### Normal Response
OutputService
→ HTTP Response
→ Raspberry Pi

### Proactive Alert
OutputService
→ OutputPublisherPort
→ WebSocketAdapter
→ Raspberry Pi

WebSocket 메시지 유형:
- output
- proactive_alert

Raspberry Pi는 Edge-TTS로 음성을 합성하고 LED, LCD, Speaker를 제어한다.

## 8. Backend API

| Method | Endpoint | Responsibility |
|---|---|---|
| POST | /api/voice | 음성 입력 처리 |
| POST | /api/tasks | 업무 등록 |
| GET | /api/tasks | 업무 목록 조회 |
| GET | /api/tasks/{id}/status | 업무 상태 확인 |
| POST | /api/tasks/{id}/complete | 업무 완료 |
| PUT | /api/tasks/{id} | 업무 수정 |

## 9. Project Structure

src/desk_pet_backend/
- api/ : HTTP 및 WebSocket API
- application/services/ : 비즈니스 유스케이스
- application/ports/ : 인터페이스 정의
- domain/models/ : 도메인 모델
- domain/enums/ : 도메인 상태
- infrastructure/eureka/ : Eureka API 연동
- infrastructure/websocket/ : 실시간 출력
- infrastructure/scheduler/ : Polling 실행
- config/ : 환경 설정

## 10. Development Status

- Architecture: 설계 완료
- Eureka API: 기존 연동 테스트 수행
- Mock Server: 기존 테스트 서버 보유
- Backend Application: 구현 예정
- Scheduler / WebSocket: 구현 예정
- Persistence: 설계 미확정

이 문서는 backend_architecture.html에 정의된 설계를 기준으로 작성한다.
실제 구현 및 테스트 완료 여부는 별도로 관리한다.
