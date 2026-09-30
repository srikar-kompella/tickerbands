#!/usr/bin/env bash
# Codespaces / devcontainer bootstrap: a throwaway demo instance so the app
# opens straight into mock mode (deterministic fake data, zero API keys) —
# no setup wizard, nothing to sign up for. run_app.sh handles deps itself.
set -euo pipefail
HOME_DIR="${TICKERLENS_HOME:-$HOME/.tickerlens}"
mkdir -p "$HOME_DIR"
if [ ! -f "$HOME_DIR/config.toml" ]; then
  cat > "$HOME_DIR/config.toml" <<'TOML'
[user]
name = "Demo"
contact_email = "demo@example.com"
TOML
fi
echo "TickerLens demo instance ready at $HOME_DIR (mock mode)."
