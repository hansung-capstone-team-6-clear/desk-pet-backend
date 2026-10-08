"""뼈대가 깨지지 않았는지 확인하는 최소 테스트: 라우팅, 토큰, WS 규약, 응답 모양, 시간 변환, mock Eureka."""

from datetime import datetime

from fastapi.testclient import TestClient

from desk_pet_backend.config import settings
from desk_pet_backend.eureka import KST, EurekaMock, from_epoch_ms, to_epoch_ms
from desk_pet_backend.main import app
from desk_pet_backend.schemas import Lcd, Output, VoiceResponse

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_text_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "device_token", "secret")
    res = client.post(
        "/api/text",
        json={"user_id": "pet-01", "request_id": "req_test_1", "text": "오늘 할 일"},
        headers={"x-device-token": "wrong"},
    )
    assert res.status_code == 401


def test_ws_connected_and_ping_pong(monkeypatch):
    monkeypatch.setattr(settings, "device_token", "secret")
    with client.websocket_connect("/ws/hardware/pet-01", headers={"x-device-token": "secret"}) as ws:
        assert ws.receive_json() == {"event": "connected", "user_id": "pet-01"}
        ws.send_text("PING")
        assert ws.receive_json() == {"event": "PONG"}


def test_voice_response_matches_contract():
    res = VoiceResponse(
        request_id="req_test_1",
        status="OK",
        stt_text="보고서 작성 추가해줘",
        type="task_created",
        output=Output(tts="보고서 작성 업무를 등록했어요.", lcd=Lcd(expression="happy", text="업무 등록 완료")),
    )
    assert set(res.model_dump()) == {"request_id", "status", "stt_text", "type", "output"}


def test_epoch_ms_round_trip_keeps_kst():
    due = datetime(2026, 10, 9, 15, 0, tzinfo=KST)
    assert to_epoch_ms(due) == 1791525600000
    assert from_epoch_ms(to_epoch_ms(due)) == due


async def test_mock_eureka_hides_done_tasks():
    eureka = EurekaMock()
    task = await eureka.create_task("보고서 검토", datetime(2026, 10, 9, 15, 0, tzinfo=KST))
    await eureka.complete_task(task.item_id)
    assert await eureka.list_open_tasks() == []
