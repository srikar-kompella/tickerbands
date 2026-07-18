"""
discussions_svc/stream.py — SSE broadcast hub.

Deviation 1 from the handoff sketch, with reason: the sketch used ONE shared
asyncio.Queue — with N open tabs each event reaches exactly one of them (queues
are work-stealing, not pub/sub). This hub keeps a queue PER subscriber and
fans events out, plus a 15s heartbeat comment so browsers/proxies don't reap
idle connections.

publish_threadsafe() is the watcher-thread → event-loop bridge: watchdog
callbacks fire on the observer thread; the loop reference is captured once at
app startup (lifespan), exactly as flagged in DESIGN_QUESTIONS §6 item 7.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

HEARTBEAT_SECONDS = 15


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle -------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once from FastAPI lifespan, on the loop SSE will run on."""
        self._loop = loop

    # -- subscription ----------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # -- publishing ------------------------------------------------------------
    def publish(self, event: dict[str, Any]) -> None:
        """Fan out from ON the event loop. Slow/full subscribers are skipped
        (they'll resync via query refetch) rather than blocking everyone."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def publish_threadsafe(self, event: dict[str, Any]) -> None:
        """Fan out from any OTHER thread (the watchdog observer)."""
        if self._loop is None or self._loop.is_closed():
            return  # nothing listening yet (startup) — events are best-effort
        self._loop.call_soon_threadsafe(self.publish, event)

    # -- SSE plumbing ----------------------------------------------------------
    async def sse_events(self) -> AsyncGenerator[str, None]:
        """One client's event stream: `data: {...}` frames + heartbeats."""
        q = self.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"  # SSE comment — keeps the pipe warm
        finally:
            self.unsubscribe(q)


broadcaster = Broadcaster()  # module-level singleton wired in app.py lifespan
