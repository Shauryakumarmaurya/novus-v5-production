"""
scripts/scheduled_refresh.py — Scheduled data refresh for the Novus RAG store.

Designed to run from cron (or any scheduler). For every ticker already in the
RAG store it:
  1. Refreshes structured financials from Screener (warms/validates the fetch
     path and logs failures early instead of at report time).
  2. Ingests any NEW documents dropped into data/raw/<TICKER>/ since the last
     run (files already present in the Chroma collection are skipped).

Crontab example — every day at 02:30:
  30 2 * * * cd "/path/to/giga-finanalytix" && venv/bin/python scripts/scheduled_refresh.py >> data/refresh.log 2>&1

Usage:
  python scripts/scheduled_refresh.py              # refresh all ingested tickers
  python scripts/scheduled_refresh.py --ticker TCS # refresh one ticker
  python scripts/scheduled_refresh.py --skip-financials
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Run from repo root regardless of cwd
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scheduled_refresh")


def refresh_financials(ticker: str) -> bool:
    """Re-fetch structured tables from Screener (bypasses the session cache)."""
    try:
        from structured_data_fetcher import get_structured_data_fetcher
        fetcher = get_structured_data_fetcher()
        fetcher._cache.pop(ticker, None)  # force a fresh scrape
        data = fetcher.fetch(ticker)
        tables = data.get("tables") or {}
        log.info(f"[{ticker}] financials OK — {len(tables)} tables")
        return bool(tables)
    except Exception as e:
        log.error(f"[{ticker}] financials refresh FAILED: {e}")
        return False


def ingest_new_documents(ticker: str) -> int:
    """Ingest files in data/raw/<TICKER>/ that aren't in the RAG store yet."""
    from rag_engine import get_collection, ingest_documents

    raw_dir = REPO_ROOT / "data" / "raw" / ticker
    if not raw_dir.is_dir():
        return 0

    collection = get_collection(ticker)

    def already_ingested(filename: str) -> bool:
        try:
            hit = collection.get(where={"filename": filename}, limit=1)
            return bool(hit and hit.get("ids"))
        except Exception:
            return False

    new_files = []
    for pattern in ("*.pdf", "*.PDF", "*.csv"):
        for path in sorted(raw_dir.glob(pattern)):
            if not already_ingested(path.name):
                new_files.append((path.name, path.read_bytes()))

    if not new_files:
        log.info(f"[{ticker}] no new documents in {raw_dir}")
        return 0

    log.info(f"[{ticker}] ingesting {len(new_files)} new document(s)...")
    result = ingest_documents(ticker, new_files)
    log.info(
        f"[{ticker}] ingested {result['total_chunks']} chunks "
        f"({result.get('failed_chunks', 0)} failed embeddings)"
    )
    return result["total_chunks"]


def _fetch_close(ticker: str):
    """Latest adjusted close via yfinance (NSE first, BSE fallback)."""
    import yfinance as yf
    for suffix in (".NS", ".BO"):
        try:
            hist = yf.Ticker(f"{ticker}{suffix}").history(period="5d", auto_adjust=True)
            if hist is not None and len(hist) and "Close" in hist.columns:
                return float(hist["Close"].iloc[-1])
        except Exception:
            continue
    return None


def evaluate_thesis_ledger() -> int:
    """Score past verdicts against actual price action at T+90 and T+180.

    A verdict 'hits' when the direction implied by the recommendation matches
    the realized move (ADD -> price up, SELL -> price down, HOLD -> within a
    +/-10% band). Outcomes are stored in the memory layer where the critic can
    read them as track-record context for future runs.
    """
    from core.memory import get_memory

    mem = get_memory()
    scored = 0
    for horizon in (90, 180):
        for row in mem.get_pending_thesis_evaluations(horizon):
            ticker = row["ticker"]
            current = _fetch_close(ticker)
            if current is None:
                log.warning(f"[{ticker}] thesis eval skipped — no price available")
                continue
            base = row["price_at_publication"]
            move_pct = round((current - base) / base * 100, 2) if base else None
            rec = (row.get("recommendation") or "").upper()
            if move_pct is None:
                hit = None
            elif "ADD" in rec or "BUY" in rec:
                hit = move_pct > 0
            elif "SELL" in rec or "SHORT" in rec:
                hit = move_pct < 0
            else:  # HOLD / NEUTRAL
                hit = abs(move_pct) <= 10.0
            evaluation = {
                "horizon_days": horizon,
                "price_then": base,
                "price_now": current,
                "move_pct": move_pct,
                "recommendation": rec,
                "hit": hit,
            }
            mem.store_thesis_evaluation(row["id"], horizon, evaluation)
            scored += 1
            log.info(
                f"[{ticker}] thesis T+{horizon}: {rec} | move {move_pct}% | "
                f"{'HIT' if hit else 'MISS' if hit is not None else 'N/A'}"
            )
    if not scored:
        log.info("Thesis ledger: nothing due for evaluation.")
    return scored


def main() -> int:
    parser = argparse.ArgumentParser(description="Scheduled Novus data refresh")
    parser.add_argument("--ticker", help="Refresh a single ticker (default: all ingested)")
    parser.add_argument("--skip-financials", action="store_true")
    parser.add_argument("--skip-documents", action="store_true")
    parser.add_argument("--skip-thesis-eval", action="store_true")
    args = parser.parse_args()

    from rag_engine import list_ingested_tickers

    if args.ticker:
        tickers = [args.ticker.upper().strip()]
    else:
        tickers = [t["ticker"] for t in list_ingested_tickers()]

    if not tickers:
        log.warning("No tickers in the RAG store — nothing to refresh.")
        return 0

    log.info(f"Refreshing {len(tickers)} ticker(s): {', '.join(tickers)}")
    failures = 0
    for ticker in tickers:
        if not args.skip_financials:
            if not refresh_financials(ticker):
                failures += 1
        if not args.skip_documents:
            try:
                ingest_new_documents(ticker)
            except Exception as e:
                log.error(f"[{ticker}] document ingest FAILED: {e}")
                failures += 1

    if not args.skip_thesis_eval:
        try:
            evaluate_thesis_ledger()
        except Exception as e:
            log.error(f"Thesis ledger evaluation FAILED: {e}")
            failures += 1

    log.info(f"Refresh complete — {failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
