#!/usr/bin/env bash
# Private ERP — setup for macOS / Linux. Safe to run again (also updates an existing install).
set -euo pipefail
cd "$(dirname "$0")"
say() { printf '\n\033[1;32m%s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m%s\033[0m\n' "$*"; exit 1; }

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || die "Python 3.12+ is required (https://www.python.org/downloads/)."
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || die "Python 3.12 or newer is required."
command -v node >/dev/null || die "Node.js 22 LTS is required (https://nodejs.org/)."

say "[1/5] Packages and web app (first run takes several minutes; later runs only redo what changed)"
if [ ! -x backend/.venv/bin/python ]; then
  "$PY" -m venv backend/.venv
  backend/.venv/bin/python -m pip install --quiet --upgrade pip
fi
# shellcheck disable=SC1091
source backend/.venv/bin/activate
tools/refresh.sh

say "[2/5] Settings and secret keys"
python backend/scripts/init_env.py

say "[3/5] Database"
(cd backend && python -m app.cli migrate)

say "[4/5] Owner account"
if (cd backend && python -m app.cli owner-exists >/dev/null 2>&1); then
  echo "Owner account already exists."
else
  email=${ERP_OWNER_EMAIL:-}; name=${ERP_OWNER_NAME:-}
  [ -n "$email" ] || read -rp "Your e-mail: " email
  [ -n "$name" ] || read -rp "Your full name: " name
  (cd backend && python -m app.cli create-owner --email "$email" --name "$name" ${ERP_OWNER_PASSWORD:+--password "$ERP_OWNER_PASSWORD"})
  demo=${ERP_DEMO:-}
  [ -n "$demo" ] || read -rp "Add a sample 'Demo' business to explore? [y/N] " demo
  if [[ "$demo" =~ ^[Yy] ]]; then
    (cd backend && python scripts/demo_data.py --email "$email" ${ERP_OWNER_PASSWORD:+--password "$ERP_OWNER_PASSWORD"})
  fi
fi


say "Done. Run ./start.sh to open the ERP (./update.sh later gets new code: git pull + start)."
echo "Your data and keys are in backend/data and backend/.env — back both up."
