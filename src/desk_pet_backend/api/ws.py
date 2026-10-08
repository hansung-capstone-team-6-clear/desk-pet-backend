"""WS /ws/{deviceId} (contracts § 2).

Pi와의 연결을 기억해 두고, services/reminder.py가 send_alert()로 알림을 보낸다.
ping/pong은 이미 동작하므로 Hardware 쪽은 지금 바로 연결 테스트를 할 수 있다.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from desk_pet_backend.api.auth import is_valid_token
from desk_pet_backend.schemas import ProactiveAlert

router = APIRouter()

connections: dict[str, WebSocket] = {}  # deviceId → 연결 (디바이스당 1개)


@router.websocket("/ws/{device_id}")
async def ws(websocket: WebSocket, device_id: str) -> None:
    if not is_valid_token(websocket.headers.get("x-device-token", "")):
        await websocket.close(code=1008)  # 정책 위반: 토큰 오류
        return

    await websocket.accept()
    connections[device_id] = websocket
    # TODO(backend, 11/14): 연결 직후 미전달 알림 재전송 (services.reminder.resend_undelivered)

    try:
        while True:
            message = await websocket.receive_json()
            if message.get("event") == "ping":
                await websocket.send_json({"event": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        if connections.get(device_id) is websocket:
            del connections[device_id]


async def send_alert(device_id: str, alert: ProactiveAlert) -> bool:
    """보냈으면 True. 연결이 없거나 보내다 실패하면 False → 호출한 쪽이 undelivered로 기록한다."""
    websocket = connections.get(device_id)
    if websocket is None:
        return False
    try:
        await websocket.send_json(alert.model_dump(by_alias=True, mode="json"))
    except (WebSocketDisconnect, RuntimeError, OSError):  # 이미 끊긴 연결
        return False
    return True
