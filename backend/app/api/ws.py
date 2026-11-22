"""WebSocket endpoint — clients subscribe to a flow's execution events."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.auth.guard import authorize_ws
from app.core.ws_manager import ws_manager

router = APIRouter()


@router.websocket("/flow/{flow_id}")
async def flow_ws(flow_id: str, websocket: WebSocket):
    # Run events carry node names, row counts and errors: signed-in only.
    # 4401 tells the app to show the sign-in screen.
    if await authorize_ws(websocket) is None:
        await websocket.accept()              # accepted first, so the browser sees the code
        await websocket.close(code=4401)
        return
    await ws_manager.connect(flow_id, websocket)
    try:
        while True:
            # Keep connection alive; client can send "ping"
            data = await websocket.receive_text()
            if data == "ping":
                # The app pings every 20 s: a session ended since (signed out,
                # password changed, idle) closes the socket within that time
                if await authorize_ws(websocket) is None:
                    ws_manager.disconnect(flow_id, websocket)
                    await websocket.close(code=4401)
                    return
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        ws_manager.disconnect(flow_id, websocket)
