"""
api/routes/ws.py
─────────────────────────────────────────────────────────────────────
WebSocket endpoint:  ws://<host>/api/v1/ws/status

Behaviour
─────────
1. On connect      → immediately receive the current pipeline snapshot.
2. On every store.update() → all connected clients receive a new JSON
   snapshot automatically (push, zero polling).
3. On terminal state (completed / failed) → server sends the final
   snapshot, then sends a closing frame {"type":"close","reason":"..."}
   and disconnects with WS close code 1000.
4. Client → server: send "ping" and receive "pong" (keepalive).
5. Server keepalive: if no update arrives for 30 s a {"ping":true}
   frame is sent to detect stale connections.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from models.schemas import JobStatus
from services.job_store import job_store

log = logging.getLogger(__name__)
router = APIRouter(tags=["WebSocket"])

_TERMINAL        = frozenset({JobStatus.completed, JobStatus.failed})
_KEEPALIVE_SECS  = 30.0


def _serialise(snapshot: dict) -> str:
    """Serialise a raw state dict (Enum values → str) to JSON."""
    return json.dumps({
        k: (v.value if hasattr(v, "value") else v)
        for k, v in snapshot.items()
    })


async def _close(websocket: WebSocket, snapshot: dict) -> None:
    """
    Send the final snapshot, a close-reason frame, then close the socket.
    Swallows all errors so the caller's finally block always runs.
    """
    status  = snapshot.get("status", "")
    reason  = "completed" if status == JobStatus.completed else "failed"
    try:
        # Final data frame
        await websocket.send_text(_serialise(snapshot))
        # Explicit close-reason frame so clients can inspect it
        await websocket.send_text(json.dumps({"type": "close", "reason": reason}))
    except Exception:
        pass
    try:
        await websocket.close(code=1000, reason=reason)
    except Exception:
        pass


@router.websocket("/ws/status")
async def pipeline_status_ws(websocket: WebSocket) -> None:
    """
    Push-based live status stream for the OSTEP pipeline.

    No parameters required — there is exactly one pipeline job.
    The server closes the connection automatically when the job reaches
    a terminal state (completed or failed).
    """
    await websocket.accept()
    client = websocket.client
    log.info("WS connected: %s", client)

    queue = job_store.subscribe()

    try:
        while True:
            # ── Wait for next snapshot ────────────────────────────
            try:
                snapshot = await asyncio.wait_for(
                    queue.get(), timeout=_KEEPALIVE_SECS
                )
            except asyncio.TimeoutError:
                try:
                    await websocket.send_text(json.dumps({"ping": True}))
                except Exception:
                    break
                continue

            # ── Terminal state: send final snapshot then disconnect ─
            if snapshot.get("status") in _TERMINAL:
                log.info(
                    "WS: pipeline terminal (%s) — disconnecting %s",
                    snapshot.get("status"), client,
                )
                await _close(websocket, snapshot)
                break

            # ── Normal update ─────────────────────────────────────
            try:
                await websocket.send_text(_serialise(snapshot))
            except (WebSocketDisconnect, RuntimeError):
                break
            except Exception as exc:
                log.warning("WS send error (%s): %s", client, exc)
                break

            # ── Non-blocking read for client frames (e.g. ping) ───
            try:
                frame = await asyncio.wait_for(
                    websocket.receive_text(), timeout=0.02
                )
                if frame.strip().lower() == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                pass
            except (WebSocketDisconnect, RuntimeError):
                break

    except WebSocketDisconnect:
        log.info("WS client disconnected: %s", client)
    except Exception as exc:
        log.exception("WS unexpected error (%s): %s", client, exc)
    finally:
        job_store.unsubscribe(queue)
        log.info("WS subscription removed: %s", client)
