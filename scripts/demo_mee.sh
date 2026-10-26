#!/usr/bin/env bash
# Start the Plains medical economics demo: Studio API (:8700), web app (:3700), and the
# onboarding proposal the demo begins with. Safe to run again: it starts only what is missing.
#
#   scripts/demo_mee.sh
#
# Then open http://localhost:3700 and follow docs/demo/mee (or the walkthrough page).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOGS="$ROOT/.studio/logs"
mkdir -p "$LOGS"

up() { curl -s -o /dev/null --max-time 2 "$1"; }

if [ ! -f .env ] || ! grep -q '^API_KEY=' .env; then
  echo "Missing .env with API_KEY / API_BASE_URL / CHAT_MODEL (the Tiger AI gateway). The agents need it." >&2
  exit 1
fi

if up http://127.0.0.1:8700/api/clients; then
  echo "API      already running on :8700"
else
  echo "API      starting on :8700 (first start builds reference data, ~1 min)"
  uv run ctx-studio init > "$LOGS/init.log" 2>&1
  nohup uv run ctx-studio serve > "$LOGS/api.log" 2>&1 &
  for _ in $(seq 60); do up http://127.0.0.1:8700/api/clients && break; sleep 2; done
fi

if up http://127.0.0.1:3700/; then
  echo "Web app  already running on :3700"
else
  echo "Web app  starting on :3700"
  (cd studio/web && { [ -d node_modules ] || npm install --silent; } && nohup npm run dev > "$LOGS/web.log" 2>&1 &)
  for _ in $(seq 60); do up http://127.0.0.1:3700/ && break; sleep 2; done
fi

# The demo starts from "Onboard Plains Health" waiting in the inbox.
uv run python - <<'EOF'
from context_studio.cli import onboard
from context_studio.config import bootstrap
from context_studio.proposals import Studio

st = Studio(bootstrap())
live = (st.s.main / "clients" / "plains_health" / "client.yaml").exists()
waiting = [r for r in st.list(status="open") if r.get("client") == "plains_health" and r["title"].startswith("Onboard")]
if live:
    print("Plains   already onboarded: pick 'Plains Health Plan' in the client picker")
elif waiting:
    print(f"Plains   onboarding proposal waiting in the inbox ({waiting[0]['id']})")
else:
    r = onboard(st, "plains_health")
    st.check(r["id"])
    print(f"Plains   onboarding proposal created ({r['id']})")
EOF

echo
echo "Open http://localhost:3700 and follow the walkthrough (docs/demo/mee/README.md)."
echo "Logs: $LOGS"
