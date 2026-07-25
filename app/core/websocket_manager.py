import asyncio
import logging
from typing import List, Dict, Any
from fastapi import WebSocket

logger = logging.getLogger("websocket_manager")


class ConnectionManager:
    """Manages active WebSocket connections for real-time messaging."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        """Accept connection and add client to active connections list."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"[WebSocket] Client connected. Total clients: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        """Safely remove a disconnected client."""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(f"[WebSocket] Client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, event_type: str, data: Dict[str, Any]):
        """Broadcast a message event to all connected WebSocket clients."""
        payload = {
            "type": event_type,
            "data": data,
        }
        async with self._lock:
            connections = list(self.active_connections)

        if not connections:
            return

        disconnected: List[WebSocket] = []
        for connection in connections:
            try:
                await connection.send_json(payload)
            except Exception as e:
                logger.warning(f"[WebSocket] Failed to send message to client: {e}")
                disconnected.append(connection)

        if disconnected:
            async with self._lock:
                for conn in disconnected:
                    if conn in self.active_connections:
                        self.active_connections.remove(conn)


manager = ConnectionManager()


async def broadcast_event(event_type: str, data: Dict[str, Any]):
    """Helper function to broadcast real-time events to all clients."""
    await manager.broadcast(event_type, data)
