"""뼈대가 깨지지 않았는지 확인하는 최소 테스트: 라우팅, 토큰, WS ping, 시간 변환, mock Eureka."""

from datetime import datetime

from fastapi.testclient import TestClient

from desk_pet_backend.config import settings
from desk_pet_backend.eureka import KST, EurekaMock, from_epoch_ms, to_epoch_ms
from desk_pet_backend.main import app
from desk_pet_backend.schemas import TextRequest

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_text_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "device_token", "secret")
    res = client.post(
        "/text",
        json={"deviceId": "pet-01", "text": "오늘 할 일"},
        headers={"x-device-token": "wrong"},
    )
    assert res.status_code == 401


def test_ws_ping_pong(monkeypatch):
    monkeypatch.setattr(settings, "device_token", "secret")
    with client.websocket_connect("/ws/pet-01", headers={"x-device-token": "secret"}) as ws:
        ws.send_json({"event": "ping"})
        assert ws.receive_json() == {"event": "pong"}


def test_json_uses_camel_case():
    assert TextRequest.model_validate({"deviceId": "pet-01", "text": "hi"}).device_id == "pet-01"


def test_epoch_ms_round_trip_keeps_kst():
    due = datetime(2026, 10, 9, 15, 0, tzinfo=KST)
    assert to_epoch_ms(due) == 1791525600000
    assert from_epoch_ms(to_epoch_ms(due)) == due


async def test_mock_eureka_hides_done_tasks():
    eureka = EurekaMock()
    task = await eureka.create_task("보고서 검토", datetime(2026, 10, 9, 15, 0, tzinfo=KST))
    await eureka.complete_task(task.item_id)
    assert await eureka.list_open_tasks() == []
