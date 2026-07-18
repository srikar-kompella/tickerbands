#!/usr/bin/env bash
# demo.sh — expose the locally-running app on a temporary PUBLIC https URL.
#
#   TICKERLENS_MOCK=1 ./demo.sh    ← RECOMMENDED for demos: fake data, real UI
#   ./demo.sh                      ← real data (see warning printed below)
#
# Uses a Cloudflare "quick tunnel": free, no account, random unguessable
# https://xxxx.trycloudflare.com URL that dies the moment you Ctrl-C.
# Only the frontend is tunneled — Vite proxies /api to the backend locally,
# so one tunnel covers the whole app (SSE included; 15s heartbeats keep it up).
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

if ! command -v cloudflared >/dev/null; then
  echo "cloudflared is not installed. One-time setup:"
  echo "    brew install cloudflared"
  exit 1
fi

if [ -z "$TICKERLENS_MOCK" ]; then
  echo "⚠️  DEMO WITH REAL DATA: anyone with the URL sees your actual Schwab"
  echo "   positions and can burn your API quota. The URL is unguessable and"
  echo "   temporary, but it has NO login. For a safe demo run:"
  echo "       TICKERLENS_MOCK=1 ./demo.sh"
  echo
  read -r -p "Continue with REAL data? [y/N] " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ] || exit 0
fi

# start the app (inherits TICKERLENS_MOCK if set); TICKERLENS_DEMO relaxes
# Vite's allowed-hosts guard for the tunnel hostname
TICKERLENS_DEMO=1 "$ROOT/run_app.sh" &
APP=$!

echo "▶ waiting for the app on :5174…"
for _ in $(seq 1 60); do
  curl -so /dev/null http://localhost:5174 && break
  sleep 1
done

echo
echo "▶ opening the public tunnel — share the https://…trycloudflare.com URL below."
echo "  Ctrl-C ends the demo: tunnel dies instantly, app stops, nothing persists."
echo
trap 'kill $APP 2>/dev/null' INT TERM EXIT
cloudflared tunnel --url http://localhost:5174
