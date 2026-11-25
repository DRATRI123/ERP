#!/usr/bin/env bash
# Get the newest code and start the ERP. Only what changed is reinstalled or rebuilt.
set -euo pipefail
cd "$(dirname "$0")"
command -v git >/dev/null || { echo "Git is not installed."; exit 1; }
[ -d .git ] || { echo "This folder is not a git copy - download the new code by hand, then run ./start.sh."; exit 1; }
echo "Getting the newest code..."
if ! git pull --ff-only; then
  echo
  echo "Could not update automatically. Usually a file was changed on this computer."
  echo "Run 'git status' to see which; 'git stash' puts your changes aside, then run ./update.sh again."
  exit 1
fi
exec ./start.sh
