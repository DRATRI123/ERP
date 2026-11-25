#!/usr/bin/env bash
# Bring packages and the web build up to date, redoing only what changed since last time.
# Sourced/called by setup.sh, start.sh and update.sh with the virtual environment active.
set -euo pipefail
cd "$(dirname "$0")/.."
redo() { local rc=0; python tools/stamp.py changed "$@" || rc=$?; [ "$rc" -eq 10 ]; }

if redo python --requires backend/.venv/bin/python -- backend/pyproject.toml; then
  echo "  Python packages changed - installing..."
  python -m pip install --quiet -e backend
  python tools/stamp.py save python -- backend/pyproject.toml
else echo "  Python packages: up to date"; fi

if redo npm --requires frontend/node_modules -- frontend/package-lock.json; then
  echo "  Web app packages changed - installing..."
  (cd frontend && npm ci --prefer-offline --no-audit --no-fund --loglevel=error)
  python tools/stamp.py save npm -- frontend/package-lock.json
else echo "  Web app packages: up to date"; fi

if redo web --requires frontend/.next/BUILD_ID -- frontend; then
  echo "  Web app code changed - building (1-3 minutes, only after an update)..."
  (cd frontend && npm run build)
  python tools/stamp.py save web -- frontend
else echo "  Web app build: up to date"; fi

if redo realtime --requires services/realtime/dist -- services/realtime; then
  if (cd services/realtime && npm ci --prefer-offline --no-audit --no-fund --loglevel=error >/dev/null 2>&1 && npm run build >/dev/null 2>&1); then
    python tools/stamp.py save realtime -- services/realtime
  fi
fi
