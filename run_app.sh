#!/usr/bin/env bash
# run_app.sh — start TickerLens (FastAPI :8001 + Vite :5174).
#
#   ./run_app.sh                normal (live Schwab/Finnhub/StockTwits data)
#   TICKERLENS_MOCK=1 ./run_app.sh    offline demo with deterministic fake data
#
# First run bootstraps its own venv + node_modules (nothing shared with the
# Portfolio app except the Schwab token, by design). Ctrl-C stops both.
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/backend/.venv"

# ── python venv (dedicated — Q13; never touch ~/schwab-env) ────────────────────
if [ ! -x "$VENV/bin/python" ]; then
  echo "▶ First run: creating venv…"
  PY_BOOT="$(command -v python3.12 || command -v python3)"
  "$PY_BOOT" -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
fi
# Re-sync deps whenever requirements.txt changed (fast no-op otherwise).
# Lesson learned: "install only when missing" skipped newly added packages.
REQ_STAMP="$VENV/.requirements.stamp"
if [ ! -f "$REQ_STAMP" ] || [ "$ROOT/backend/requirements.txt" -nt "$REQ_STAMP" ]; then
  echo "▶ Syncing backend deps…"
  "$VENV/bin/pip" install --quiet -r "$ROOT/backend/requirements.txt"
  touch "$REQ_STAMP"
fi

# ── node modules — same rule: re-sync when package.json changed ────────────────
NODE_STAMP="$ROOT/frontend/node_modules/.install.stamp"
if [ ! -f "$NODE_STAMP" ] || [ "$ROOT/frontend/package.json" -nt "$NODE_STAMP" ]; then
  echo "▶ Syncing frontend deps…"
  (cd "$ROOT/frontend" && npm install --no-fund --no-audit)
  touch "$NODE_STAMP"
fi

# A previous instance that wasn't Ctrl-C'd (e.g. terminal window closed) keeps
# the ports bound and the next start dies with "Address already in use" —
# these ports are ours by contract, so clear them before starting.
# Kill by command pattern FIRST: killing only the listener leaves uvicorn's
# reloader parent alive, which instantly respawns it (observed in the wild).
pkill -f "uvicorn app:app --port 8001" 2>/dev/null && sleep 1 || true
for PORT in 8001 5174; do
  for _try in 1 2 3; do
    STALE=$(lsof -ti tcp:$PORT -sTCP:LISTEN 2>/dev/null || true)  # LISTEN only — never kill clients
    [ -z "$STALE" ] && break
    echo "▶ clearing stale process on :$PORT (pid $STALE, attempt $_try)"
    kill $STALE 2>/dev/null || true
    sleep 1
    [ "$_try" = 3 ] && kill -9 $STALE 2>/dev/null || true
  done
done

echo "▶ Backend  : http://localhost:8001  (docs at /docs)"
echo "▶ Frontend : http://localhost:5174"
[ -n "$TICKERLENS_MOCK" ] && echo "▶ MOCK MODE — deterministic fake data, no live APIs"
echo

# --reload scoped to *.py: backend code changes apply without a restart
# (DB/cache writes in this dir must NOT trigger reloads — hence the include).
( cd "$ROOT/backend" && exec "$VENV/bin/uvicorn" app:app --port 8001 \
    --reload --reload-include '*.py' ) &
BACK=$!

( cd "$ROOT/frontend" && exec npm run dev ) &
FRONT=$!

# Auto-open the browser once the frontend answers (skip: TICKERLENS_NO_OPEN=1).
if [ -z "$TICKERLENS_NO_OPEN" ] && command -v open >/dev/null; then
  ( for _ in $(seq 1 60); do
      curl -so /dev/null http://localhost:5174 && { open http://localhost:5174; break; }
      sleep 1
    done ) &
fi

trap 'echo; echo "stopping…"; kill $BACK $FRONT 2>/dev/null' INT TERM EXIT
wait
