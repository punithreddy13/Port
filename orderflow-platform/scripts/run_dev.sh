#!/usr/bin/env bash
# Dev server: simulated feed, auto-reload, http://localhost:8720
set -euo pipefail
cd "$(dirname "$0")/../backend"
export PYTHONPATH="$PWD"
exec python3 -m uvicorn orderflow.server.app:app --host 0.0.0.0 --port "${ORDERFLOW_PORT:-8720}" --reload
