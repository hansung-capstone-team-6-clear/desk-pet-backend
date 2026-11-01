"""뼈대가 깨지지 않았는지 확인하는 최소 테스트: 라우팅, 토큰, WS 규약, 응답 모양, 시간 변환, mock Eureka."""

import re
import wave
from datetime import datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from desk_pet_backend.api import voice
from desk_pet_backend.config import settings
from desk_pet_backend.eureka import KST, EurekaMock, from_epoch_ms, to_epoch_ms
from desk_pet_backend.main import app
from desk_pet_backend.schemas import Decision, DecisionArgs, Lcd, Output, VoiceResponse

client = TestClient(app)


def make_wav(*, rate=16_000, channels=1, width=2, frames=16_000):
    output = BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(width)
        recording.setframerate(rate)
        recording.writeframes(b"\0" * frames * channels * width)
    return output.getvalue()


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_text_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "device_token", "secret")
    res = client.post(
        "/api/text",
        json={"user_id": "pet-01", "text": "오늘 할 일"},
        headers={"x-device-token": "wrong"},
    )
    assert res.status_code == 401


def test_text_passes_request_to_task_handler(monkeypatch):
    monkeypatch.setattr(settings, "device_token", "secret")
    received = {}

    async def fake_handle(**kwargs):
        received.update(kwargs)
        return VoiceResponse(
            request_id=kwargs["request_id"],
            status="OK",
            stt_text=kwargs["text"],
            type="task_list",
            output=Output(tts="미완료 업무가 없어요.", lcd=Lcd(expression="speaking", text="업무 0건")),
        )

    monkeypatch.setattr(voice.tasks, "handle", fake_handle)
    res = client.post(
        "/api/text",
        json={"user_id": "pet-01", "text": "오늘 할 일"},
        headers={"x-device-token": "secret"},
    )
    assert res.status_code == 200
    assert received["user_id"] == "pet-01"
    assert received["text"] == "오늘 할 일"
    assert re.fullmatch(r"req_\d{8}_\d{6}_[0-9a-f]{4}", received["request_id"])
    assert res.json()["request_id"] == received["request_id"]
    assert res.json()["type"] == "task_list"
    assert res.json()["stt_text"] == "오늘 할 일"


def test_voice_passes_valid_wav_to_ai_and_returns_contract_response(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "device_token", "secret")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "voice.db"))
    monkeypatch.setattr(voice.tasks, "get_eureka", lambda: EurekaMock())
    received = {}

    async def fake_analyze(**kwargs):
        received.update(kwargs)
        return Decision(
            transcript="오늘 할 일 알려줘",
            action="list_tasks",
            args=DecisionArgs(range="today"),
        )

    monkeypatch.setattr(voice.tasks.ai, "analyze", fake_analyze)
    wav = make_wav()
    res = client.post(
        "/api/voice",
        data={"user_id": "pet-01"},
        files={"audio": ("speech.wav", wav, "audio/wav")},
        headers={"x-device-token": "secret"},
    )
    assert res.status_code == 200
    assert res.json()["request_id"].startswith("req_")
    assert res.json()["status"] == "OK"
    assert res.json()["type"] == "task_list"
    assert res.json()["stt_text"] == "오늘 할 일 알려줘"
    assert received["audio"] == wav
    assert received["text"] is None


@pytest.mark.parametrize("wav, status_code", [
    (b"not a wav", 422),
    (make_wav(rate=8_000), 422),
    (make_wav(channels=2), 422),
    (make_wav(width=1), 422),
    (make_wav(frames=0), 422),
    (make_wav()[:-10], 422),
    (make_wav(frames=16_000 * 15 + 1), 413),
    (b"x" * 1_000_001, 413),
])
def test_voice_rejects_invalid_wav(monkeypatch, wav, status_code):
    monkeypatch.setattr(settings, "device_token", "secret")
    res = client.post(
        "/api/voice",
        data={"user_id": "pet-01"},
        files={"audio": ("speech.wav", wav, "audio/wav")},
        headers={"x-device-token": "secret"},
    )
    assert res.status_code == status_code


def test_voice_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "device_token", "secret")
    res = client.post(
        "/api/voice",
        data={"user_id": "pet-01"},
        files={"audio": ("speech.wav", make_wav(), "audio/wav")},
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
