"""WS /ws/hardware/{user_id} (contracts § 2).

이전 선제 알림 설계의 연결이다. 알림 전송은 지웠고 (2026-11-06 결정: 마감 알람은 Pi가 GET /api/tasks로 직접 울린다),
Hardware 앱이 아직 이 주소에 연결하므로 connected·PING/PONG만 남긴다. Pi가 연결을 끊으면 이 파일도 지운다.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from desk_pet_backend.api.auth import is_valid_token

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

    try:
        while True:
            if (await websocket.receive_text()).strip() == "PING":
                await websocket.send_json({"event": "PONG"})
    except WebSocketDisconnect:
        pass
    finally:
        if connections.get(user_id) is websocket:
            del connections[user_id]

