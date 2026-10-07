# Desk Pet Backend

AI 기반 데스크 펫 로봇의 백엔드 서버입니다.

## 주요 기능
- FastAPI 기반 REST API
- Eureka Process API 연동
- 업무 등록, 조회, 수정, 완료 처리
- Polling 기반 업무 마감 알림
- WebSocket 실시간 알림 전송
- LLM 및 MCP 연계
- Raspberry Pi 하드웨어 출력 명령 전달

## 기술 스택
- Python / FastAPI
- REST API / WebSocket
- Eureka Process API
- LLM / MCP
- Docker / Docker Compose

## 아키텍처
Ports & Adapters 기반 계층형 구조

- api: HTTP 및 WebSocket 인터페이스
- application: 업무 처리 서비스 및 포트
- domain: 도메인 모델과 비즈니스 규칙
- infrastructure: Eureka, Scheduler, WebSocket 어댑터
- config: 환경 설정

## 핵심 서비스
- TaskService: 업무 관리
- ReminderService: 알림 조건 판단
- OutputService: 출력 명령 생성

## 알림 흐름
SchedulerAdapter
→ ReminderService
→ OutputService
→ OutputPublisherPort
→ WebSocketAdapter
→ Raspberry Pi (TTS / LED / LCD)

기본 알림: 마감 1시간 전
사용자 지정 알림 시간 우선 적용

## 디렉터리
- src/desk_pet_backend: 백엔드 소스 코드
- mocks: Mock 서버 및 테스트 시나리오
- tests: 단위 / 통합 / E2E 테스트
- deployment: 클라우드 배포 설정
- scripts: 개발 및 운영 스크립트
- docs: 아키텍처 문서

## 개발 상태
초기 프로젝트 구조 구성 단계.
API 및 주요 서비스 구현 예정.
