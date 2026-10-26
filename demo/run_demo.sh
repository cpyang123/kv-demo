#!/bin/bash
# Start the Knowledge Vault × ChainSWE demo server. Usage: demo/run_demo.sh [port]
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${VENV:-/scratch/$USER/venvs/kv-demo}"
export RSM_SIF_DIR="${RSM_SIF_DIR:-/scratch/$USER/kv/sifs}"
export DEMO_PORT="${1:-${DEMO_PORT:-8765}}"
echo "demo at http://${DEMO_HOST:-127.0.0.1}:$DEMO_PORT  (runs: ${RSM_RUNS_DIR:-$PWD/runs}, sifs: $RSM_SIF_DIR)"
exec "$VENV/bin/python" demo/server.py
