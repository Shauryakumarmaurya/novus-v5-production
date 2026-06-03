#!/bin/bash
cd "$(dirname "$0")/.."
source venv/bin/activate
while true; do
    echo "Starting worker..."
    python worker.py
    echo "Worker crashed with exit code $?. Restarting in 5 seconds..."
    sleep 5
done
