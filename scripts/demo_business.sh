#!/usr/bin/env bash
# The business demo: a medical economics desk on Plains Health, on its own Studio home and ports so it
# never disturbs the Studio demo. Builds the home once (three approved changes), then starts the API
# (:8702) and web app (:3702). Safe to run again: it starts only what is missing.
#
#   scripts/demo_business.sh            # then open http://localhost:3702/insights
#   scripts/demo_business.sh --fresh    # rebuild the demo home from scratch
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export CTX_STUDIO_HOME="$ROOT/.studio-business"
LOGS="$CTX_STUDIO_HOME/logs"
API=8702 WEB=3702
up() { curl -s -o /dev/null --max-time 2 "$1"; }

if [ ! -f .env ] || ! grep -q '^API_KEY=' .env; then
  echo "Missing .env with API_KEY / API_BASE_URL / CHAT_MODEL (the Tiger AI gateway). The analyst needs it." >&2
  exit 1
fi
if [ "${1:-}" = "--fresh" ]; then
  pkill -f "ctx-studio serve --port $API" || true
  rm -rf "$CTX_STUDIO_HOME"
fi
mkdir -p "$LOGS"

if [ ! -d "$CTX_STUDIO_HOME/main" ]; then
  echo "Demo home  building (first time, about a minute)"
  uv run ctx-studio init > "$LOGS/init.log" 2>&1
fi
uv run python scripts/demo_business_state.py 2>&1 | grep -v Warning | sed 's/^/Demo state  /'

if up "http://127.0.0.1:$API/api/clients"; then
  echo "API        already running on :$API"
else
  echo "API        starting on :$API"
  nohup uv run ctx-studio serve --port "$API" > "$LOGS/api.log" 2>&1 &
  for _ in $(seq 60); do up "http://127.0.0.1:$API/api/clients" && break; sleep 2; done
fi

if up "http://127.0.0.1:$WEB/insights"; then
  echo "Web app    already running on :$WEB"
else
  echo "Web app    starting on :$WEB"
  (cd studio/web && { [ -d node_modules ] || npm install --silent; } \
    && NEXT_DIST_DIR=.next-business NEXT_PUBLIC_STUDIO_API="http://127.0.0.1:$API" \
       nohup npx next dev -p "$WEB" > "$LOGS/web.log" 2>&1 &)
  for _ in $(seq 90); do up "http://127.0.0.1:$WEB/insights" && break; sleep 2; done
fi

echo
echo "Open http://localhost:$WEB/insights  (pick Plains Health Plan if another client shows)."
echo "Logs: $LOGS"
