#!/usr/bin/env bash
# Put the ERP on the internet while this runs (free Cloudflare tunnel). Ctrl+C to stop.
cd "$(dirname "$0")"
exec "${PYTHON:-python3}" tools/online.py
