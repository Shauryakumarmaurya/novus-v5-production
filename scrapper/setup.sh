#!/usr/bin/env bash
# setup.sh — Create virtual environment and install dependencies
# Run once before using the scraper:  bash setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Screener Docs Scraper — Environment Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Create virtual environment ────────────────────────────────────────────
if [ -d "$VENV_DIR" ]; then
    echo "✓ Virtual environment already exists at .venv"
else
    echo "→ Creating virtual environment at .venv …"
    python3 -m venv "$VENV_DIR"
    echo "✓ Virtual environment created."
fi

# ── 2. Activate & install dependencies ───────────────────────────────────────
echo "→ Installing dependencies from requirements.txt …"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" --quiet

echo ""
echo "✓ All dependencies installed successfully."
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  To run the scraper:"
echo ""
echo "  # Activate the venv"
echo "  source .venv/bin/activate"
echo ""
echo "  # Download docs for ALL stocks (slow — thousands of stocks)"
echo "  python scrape_screener_docs.py"
echo ""
echo "  # Download docs for specific stocks only"
echo "  python scrape_screener_docs.py --symbols HUL TCS INFY RELIANCE"
echo ""
echo "  # Test with just 5 stocks first"
echo "  python scrape_screener_docs.py --limit 5"
echo ""
echo "  # Deactivate when done"
echo "  deactivate"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
