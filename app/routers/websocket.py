import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.websocket_manager import manager

router = APIRouter()
logger = logging.getLogger("websocket_router")


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time messaging updates."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError):
        await manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"[WebSocket Endpoint] Connection closed with message: {e}")
        await manager.disconnect(websocket)
