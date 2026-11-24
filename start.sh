#!/usr/bin/env bash
# Private ERP — start everything locally (macOS / Linux). Ctrl+C stops it all.
set -euo pipefail
cd "$(dirname "$0")"
[ -x backend/.venv/bin/python ] && [ -f frontend/.next/BUILD_ID ] || { echo "Run ./setup.sh first."; exit 1; }
# shellcheck disable=SC1091
source backend/.venv/bin/activate
(cd backend && python -m app.cli migrate >/dev/null)
mkdir -p backend/data/logs
pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; echo; echo "Private ERP stopped."; }
trap cleanup EXIT INT TERM

(cd backend && exec python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000) > backend/data/logs/api.log 2>&1 & pids+=($!)
(cd backend && exec python -m app.cli worker) > backend/data/logs/worker.log 2>&1 & pids+=($!)
(cd frontend && exec npm run start) > backend/data/logs/web.log 2>&1 & pids+=($!)
redis=$(grep -E '^ERP_REDIS_URL=redis' backend/.env | cut -d= -f2- || true)
if [ -n "$redis" ] && [ -f services/realtime/dist/src/index.js ]; then
  (cd services/realtime && ERP_REDIS_URL="$redis" ERP_API_URL=http://127.0.0.1:8000 exec node dist/src/index.js) > backend/data/logs/realtime.log 2>&1 & pids+=($!)
fi

for _ in $(seq 1 60); do curl -fs -o /dev/null http://localhost:3000/login && break; sleep 1; done
echo "Private ERP is running at http://localhost:3000  (logs in backend/data/logs; Ctrl+C to stop)"
(command -v open >/dev/null && open http://localhost:3000) || (command -v xdg-open >/dev/null && xdg-open http://localhost:3000 >/dev/null 2>&1) || true
wait
