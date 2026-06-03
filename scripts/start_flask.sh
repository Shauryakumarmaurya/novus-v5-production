#!/bin/bash
# Novus Flask Server Launcher — called by launchd

REPO_DIR="/Users/shauryaiitd/Desktop/giga-finanalytix copy 2"
cd "$REPO_DIR"

# Load .env silently (ignore errors)
if [ -f "$REPO_DIR/.env" ]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
        export "$key"="$value"
    done < "$REPO_DIR/.env"
fi

export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export GRPC_ENABLE_FORK_SUPPORT=1
export PATH="$REPO_DIR/venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export HOME="/Users/shauryaiitd"

exec "$REPO_DIR/venv/bin/python3" app.py
