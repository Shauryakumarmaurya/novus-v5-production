#!/bin/bash
# Novus Start Script — run this once after any reboot or sleep
# Double-click from Finder or run: bash scripts/novus_start.sh

REPO="/Users/shauryaiitd/Desktop/giga-finanalytix copy 2"
PYTHON="$REPO/venv/bin/python3"

echo "🚀 Starting Novus FinLLM services..."

# Kill any stale processes
pkill -f "python3 app.py" 2>/dev/null
pkill -f "python3 worker.py" 2>/dev/null
sleep 1

# Load environment
cd "$REPO"
if [ -f "$REPO/.env" ]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
        # Strip trailing carriage returns, then strip opening and closing quotes
        value=$(echo "$value" | tr -d '\r')
        value="${value%\"}"
        value="${value#\"}"
        export "$key"="$value"
    done < "$REPO/.env"
fi
export no_proxy="*"

export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export GRPC_ENABLE_FORK_SUPPORT=1
export PATH="$REPO/venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# Start Flask
nohup "$PYTHON" -u app.py > "$REPO/server.log" 2>&1 &
FLASK_PID=$!
echo "  ✅ Flask started (PID $FLASK_PID)"

sleep 2

# Start Worker  
nohup "$PYTHON" -u worker.py > "$REPO/worker.log" 2>&1 &
WORKER_PID=$!
echo "  ✅ Worker started (PID $WORKER_PID)"

sleep 3

# Verify
if curl -s --max-time 3 http://localhost:5001/ | head -1 | grep -q "DOCTYPE"; then
    echo ""
    echo "✅ Novus is UP → http://localhost:5001"
else
    echo ""
    echo "⚠️  Flask may still be starting — check server.log if needed"
fi
