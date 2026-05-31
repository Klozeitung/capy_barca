"""
WebSocket router.

Exposes the /ws endpoint. Authentication is performed by validating the
session cookie that arrives with the HTTP upgrade handshake – the same
cookie already used by all REST endpoints, so no additional token passing
is required.

An absent or invalid session causes an immediate close with application
code 4401 (chosen to mirror HTTP 401; codes ≥ 4000 are reserved for
application-defined use by the WebSocket RFC).

Protocol (server → client):
    {"type": "block.event", "payload": {...}}   – block mutation event
    {"type": "pong"}                             – response to client ping

Protocol (client → server):
    {"type": "ping"}                             – keepalive probe
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Cookie, WebSocket, WebSocketDisconnect

from app.session.session import validate_token
from app.ws.manager import manager

ws_router = APIRouter(tags=["websocket"])

logger = logging.getLogger(__name__)

# Application-defined close code signalling unauthenticated access.
_CLOSE_UNAUTHORIZED = 4401


@ws_router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    session: Optional[str] = Cookie(default=None),
) -> None:
    """
    WebSocket endpoint for real-time block events.

    The client must carry a valid ``session`` cookie (the same one issued
    by ``POST /api/login``). No token is passed in the URL to avoid it
    appearing in access logs.
    """
    if not session or not validate_token(session):
        await websocket.close(code=_CLOSE_UNAUTHORIZED)
        return

    await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("WS received non-JSON frame, ignoring")
                continue
            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
