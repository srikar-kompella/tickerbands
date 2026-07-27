"""
app.py — FastAPI entrypoint. Thin by design: wiring only.

Run (dev):   .venv/bin/uvicorn app:app --port 8001          (from backend/)
Mock mode:   TICKERLENS_MOCK=1 .venv/bin/uvicorn app:app --port 8001
Docs:        http://localhost:8001/docs

Lifespan owns three things (deviation 4: lifespan, not the deprecated
@app.on_event): DB init, binding the SSE broadcaster to THIS event loop
(the watcher thread needs the reference — deviation 7), and the discussions
watcher (start + catch-up scan for files written while the app was down).
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from api.routes import router
from api.setup import router as setup_router
from db import init_db
from discussions_svc.stream import broadcaster
from discussions_svc.watcher import catch_up_scan, start_watcher


def _seed_watchlist_once() -> int:
    """Apply backend/seed_watchlist.json exactly once (settings-flag guarded).

    Exists because seeding the DB remotely proved fragile (SQLite WAL over a
    mounted FS) — the seed now ships WITH the app and applies natively on the
    machine that runs it. Deleting symbols later is respected: the flag means
    this never runs again.
    """
    import json
    import os
    from db import connect

    if os.environ.get("TICKERLENS_SKIP_SEED"):  # tests want empty watchlists
        return 0
    seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "seed_watchlist.json")
    if not os.path.exists(seed_path):
        return 0
    conn = connect()
    try:
        if conn.execute("SELECT 1 FROM settings WHERE key='watchlist_seeded'").fetchone():
            return 0
        with open(seed_path) as f:
            seed = json.load(f)
        import datetime as dt
        now = dt.datetime.now().isoformat(timespec="seconds")
        added = 0
        for sym in seed.get("symbols", []):
            cur = conn.execute(
                "INSERT OR IGNORE INTO watchlist (symbol, note, tags, added_ts) "
                "VALUES (?, '', ?, ?)",
                (sym.upper(), json.dumps([seed.get("tag", "seed")]), now))
            added += cur.rowcount
        conn.execute("INSERT OR REPLACE INTO settings (key, value) "
                     "VALUES ('watchlist_seeded', '1')")
        conn.commit()
        return added
    finally:
        conn.close()


async def _auto_refresh_loop():
    """His #2: while the app is OPEN, keep watchlist scores fresh (~hourly).
    No daemons — dies with the process. Staggered 6s/symbol for Finnhub's
    60/min; TTL caches make repeat passes cheap. Every pass feeds the score
    audit with real scored days."""
    import asyncio as aio
    from db import connect
    await aio.sleep(180)   # let startup + first interactive use settle
    while True:
        try:
            conn = connect()
            syms = [r["symbol"] for r in conn.execute("SELECT symbol FROM watchlist")]
            conn.close()
            from analysis import composer as comp
            for s in syms:
                try:
                    await aio.to_thread(comp.analyze, s)
                except Exception:
                    pass          # one bad symbol never stops the pass
                await aio.sleep(6)
            print(f"[tickerlens] auto-refresh pass done ({len(syms)} symbols)")
        except Exception:
            pass
        await aio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seeded = _seed_watchlist_once()
    if seeded:
        print(f"[tickerlens] seeded watchlist with {seeded} symbols from seed_watchlist.json")
    broadcaster.bind_loop(asyncio.get_running_loop())
    observer = start_watcher()
    refresh_task = None
    if not os.environ.get("TICKERLENS_NO_AUTOREFRESH"):
        refresh_task = asyncio.create_task(_auto_refresh_loop())
    scanned = catch_up_scan()
    print(f"[tickerlens] watcher up on discussions/ (catch-up scanned {scanned} files)")
    yield
    if refresh_task:
        refresh_task.cancel()
    observer.stop()
    observer.join(timeout=3)


app = FastAPI(
    title="TickerLens API",
    version="0.1.0",
    description="Research tool — calibrated ranges, transparent signals, "
                "zero direction predictions.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.FRONTEND_ORIGINS,  # :5174 — Portfolio app owns :5173
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(setup_router)
