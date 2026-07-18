# Demoing TickerLens over a public URL

Run the app on your Mac, share a temporary `https://…trycloudflare.com` link,
demo from any device. No router changes, no port forwarding, no account.

## One-time setup

```bash
brew install cloudflared
```

## Run a demo (recommended: mock data)

```bash
cd ~/Code/tickerlens
TICKERLENS_MOCK=1 ./demo.sh
```

- Starts the app (backend + frontend) and opens the tunnel.
- The shareable URL prints in the terminal — look for the boxed line:
  `https://<random-words>.trycloudflare.com`
- Mock mode = the full real UI with deterministic fake data. **Use this for
  demos** — nothing about your money is visible.

## Demo with REAL data (think first)

```bash
./demo.sh
```

The script stops and asks for confirmation, because you should read this:

- The URL has **no login**. It is random and unguessable, but anyone who has
  it sees your actual Schwab positions, watchlist, discussions, and can
  trigger API calls that burn your Schwab/Finnhub quota.
- The API is read-only by construction — nobody can trade or change anything —
  but *seeing* is exposure enough. Only share with someone you'd show your
  brokerage screen to.

## Stopping

`Ctrl-C` in the terminal. The public URL dies instantly, the app stops,
nothing persists on the internet. Every run generates a fresh URL.

## How it works (30 seconds)

`demo.sh` starts the normal app, then runs
`cloudflared tunnel --url http://localhost:5174`. Cloudflare assigns a random
subdomain and relays traffic to your Mac over an outbound connection — no
inbound ports are ever opened. Only the frontend (5174) is tunneled: Vite
proxies `/api/*` to the backend (8001) locally, so one tunnel carries the
whole app, including the live discussion updates (SSE with 15s heartbeats).
`TICKERLENS_DEMO=1` (set by the script) tells Vite to accept the tunnel's
hostname — without it, Vite's DNS-rebinding protection blocks the requests.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cloudflared: command not found` | `brew install cloudflared` |
| Page loads but says "Blocked request. This host is not allowed" | You started the app yourself instead of via `demo.sh` — use `./demo.sh` (it sets `TICKERLENS_DEMO=1`) |
| Viewer sees data but no live discussion updates | Corporate proxies on *their* side sometimes buffer SSE; the timeline still updates on tab refresh |
| Tunnel URL slow or erroring under load | Quick tunnels are capped (~200 concurrent requests) and are for demos, not hosting |
| Need it up permanently / with a login | Different tool: a *named* Cloudflare Tunnel + Cloudflare Access (free tier, ~30 min setup) — ask for it if you actually need this |

## Limits — read once

Quick tunnels are explicitly a demo/testing feature: ephemeral, unauthenticated,
rate-capped, and Cloudflare offers no uptime promise on them. If a demo matters
(interview, investor, whatever), do a dry run 10 minutes before.
