"""WS /ws/hardware/{user_id} (contracts § 2).

Pi와의 연결을 기억해 두고, services/reminder.py가 send_alert()로 알림을 보낸다.
connected·PING/PONG은 이미 동작하므로 Hardware 쪽은 지금 바로 연결 테스트를 할 수 있다.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from desk_pet_backend.api.auth import is_valid_token
from desk_pet_backend.schemas import ProactiveAlert

router = APIRouter()

connections: dict[str, WebSocket] = {}  # user_id → 연결 (MVP: 사용자 1명당 펫 1대)


@router.websocket("/ws/hardware/{user_id}")
async def ws(websocket: WebSocket, user_id: str) -> None:
    if not is_valid_token(websocket.headers.get("x-device-token", "")):
        await websocket.close(code=1008)  # 정책 위반: 토큰 오류
        return

    await websocket.accept()
    connections[user_id] = websocket
    await websocket.send_json({"event": "connected", "user_id": user_id})
    # TODO(backend, 11/14): 연결 직후 미전달 알림 재전송 (services.reminder.resend_undelivered)

    try:
        while True:
            if (await websocket.receive_text()).strip() == "PING":
                await websocket.send_json({"event": "PONG"})
    except WebSocketDisconnect:
        pass
    finally:
        if connections.get(user_id) is websocket:
            del connections[user_id]


async def send_alert(user_id: str, alert: ProactiveAlert) -> bool:
    """보냈으면 True. 연결이 없거나 보내다 실패하면 False → 호출한 쪽이 undelivered로 기록한다."""
    websocket = connections.get(user_id)
    if websocket is None:
        return False
    try:
        await websocket.send_json(alert.model_dump(mode="json"))
    except (WebSocketDisconnect, RuntimeError, OSError):  # 이미 끊긴 연결
        return False
    return True
