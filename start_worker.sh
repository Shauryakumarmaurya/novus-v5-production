#!/bin/bash
# Self-healing RQ worker — auto-restarts on Redis timeout/disconnect
# Usage: ./start_worker.sh

export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export TOKENIZERS_PARALLELISM=false

cd "$(dirname "$0")"
source venv/bin/activate

echo "[worker] Starting self-healing RQ worker..."

while true; do
    echo "[worker] $(date '+%Y-%m-%d %H:%M:%S') — Worker starting..."
    rq worker financial_analysis
    EXIT_CODE=$?
    echo "[worker] $(date '+%Y-%m-%d %H:%M:%S') — Worker exited with code $EXIT_CODE. Restarting in 3s..."
    sleep 3
done
