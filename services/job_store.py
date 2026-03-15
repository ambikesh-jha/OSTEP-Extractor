"""
services/job_store.py
─────────────────────────────────────────────────────────────────────
Single-pipeline job store.

Design rules
────────────
• There is exactly ONE pipeline job for the entire application lifetime,
  identified by the fixed constant PIPELINE_JOB_ID.
• /scrape/start creates/resets this job only when no active run exists
  (status not in {pending, running}).  Otherwise it returns the current
  job_id so the caller can subscribe to the WebSocket.
• /merge/start reuses the same job_id and advances the pipeline phase.
• WebSocket subscribers register an asyncio.Queue here; every update()
  call fans out a JSON snapshot to all live queues thread-safely via
  loop.call_soon_threadsafe.
"""

from __future__ import annotations

import asyncio
import copy
import threading
from datetime import datetime, timezone
from typing import Any

from models.schemas import JobStatus, PipelinePhase

# Fixed, well-known pipeline job identifier
PIPELINE_JOB_ID = "ostep-pipeline"

_INITIAL_STATE: dict[str, Any] = {
    "job_id":            PIPELINE_JOB_ID,
    "phase":             PipelinePhase.idle,
    "status":            JobStatus.idle,
    # scrape fields
    "chapters_found":    0,
    "chapters_fetched":  0,
    "assets_downloaded": 0,
    "scrape_output":     None,
    # merge fields
    "chapters_merged":   0,
    "total_pages":       0,
    "file_size_mb":      0.0,
    "merge_output":      None,
    "missing_files":     [],
    # shared
    "error":             None,
    "started_at":        None,
    "updated_at":        None,
}


class _Subscriber:
    """
    Pairs an asyncio.Queue with the event loop that owns it so
    put_nowait can be dispatched safely from any thread.
    """
    __slots__ = ("queue", "loop")

    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
        self.queue = queue
        self.loop  = loop


class JobStore:
    """
    Thread-safe store for the single OSTEP pipeline job.

    WebSocket consumers call subscribe() / unsubscribe() to receive a
    copy of the state dict every time update() is called.  Fanout is
    dispatched via loop.call_soon_threadsafe so it is safe to call
    update() from any background thread.
    """

    def __init__(self) -> None:
        self._state: dict[str, Any] = copy.deepcopy(_INITIAL_STATE)
        self._lock  = threading.RLock()
        self._subscribers: list[_Subscriber] = []
        self._sub_lock = threading.Lock()

    # ── Read ───────────────────────────────────────────────────────

    def get(self) -> dict[str, Any]:
        with self._lock:
            return copy.copy(self._state)

    def is_active(self) -> bool:
        """True when a scrape or merge is currently pending/running."""
        with self._lock:
            return self._state["status"] in (JobStatus.pending, JobStatus.running)

    # ── Write ──────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset pipeline to initial state (called by /scrape/start)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._state = copy.deepcopy(_INITIAL_STATE)
            self._state["status"]     = JobStatus.pending
            self._state["started_at"] = now
            self._state["updated_at"] = now
            snapshot = copy.copy(self._state)
        self._fanout(snapshot)

    def update(self, **fields) -> None:
        with self._lock:
            self._state.update(fields)
            self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
            snapshot = copy.copy(self._state)
        self._fanout(snapshot)

    # ── WebSocket pub/sub ──────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        """
        Register a new WebSocket listener.
        Must be called from inside a running asyncio event loop.
        Returns the queue the caller should await on.

        Queue size is 1 — only the LATEST snapshot matters.
        The WS handler drains and re-queues continuously, so a size-1
        queue with replace-on-full semantics means we always deliver
        the most recent state and never drop the terminal update.
        """
        loop  = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        sub   = _Subscriber(queue=queue, loop=loop)
        with self._sub_lock:
            self._subscribers.append(sub)
        # Seed with current state immediately
        try:
            queue.put_nowait(self.get())
        except asyncio.QueueFull:
            pass
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._sub_lock:
            self._subscribers = [s for s in self._subscribers if s.queue is not queue]

    def _fanout(self, snapshot: dict) -> None:
        """
        Deliver the latest snapshot to every subscriber.
        Called from background threads via call_soon_threadsafe.

        Uses replace-on-full semantics: if the queue already holds a
        stale snapshot (not yet consumed by the WS handler), discard it
        and put the new one.  This guarantees the LATEST state is always
        deliverable, including the critical terminal (completed/failed)
        update that closes the connection.
        """
        def _put(queue: asyncio.Queue, snap: dict) -> None:
            # Drain any stale item first, then put the fresh snapshot.
            # Because maxsize=1 this is always at most one discard.
            try:
                queue.get_nowait()   # discard stale
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(snapshot)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()   # remove oldest
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(snapshot)

        with self._sub_lock:
            dead: list[asyncio.Queue] = []
            for sub in self._subscribers:
                try:
                    sub.loop.call_soon_threadsafe(_put, sub.queue, snapshot)
                except RuntimeError:
                    dead.append(sub.queue)
                except Exception:
                    dead.append(sub.queue)
            if dead:
                self._subscribers = [
                    s for s in self._subscribers if s.queue not in dead
                ]


# Application-wide singleton
job_store = JobStore()

