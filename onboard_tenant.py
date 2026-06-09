"""
onboard_tenant.py — Onboard a new company into the Novus RAG store (ChromaDB).

This is the SINGLE ingestion path for new tickers. It uses
rag_engine.ingest_documents(), which already handles:
  - doc-type classification (annual report / transcript / quarterly / ...)
  - hybrid vision parsing for table-heavy pages
  - section-aware chunking + fiscal period stamping
  - Voyage embeddings + ChromaDB upsert (same store the app queries)

The legacy Qdrant path was removed: documents written to Qdrant were never
read by the product (rag_engine/copilot/CIO pipeline all query ChromaDB).

Usage:
    python onboard_tenant.py SUNPHARMA
    python onboard_tenant.py SUNPHARMA --folder /path/to/pdfs
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def collect_documents(folder: Path) -> list[tuple[str, bytes]]:
    """Gather (filename, bytes) for every PDF/CSV under the folder (recursive)."""
    files_data = []
    for pattern in ("**/*.pdf", "**/*.PDF", "**/*.csv"):
        for path in sorted(folder.glob(pattern)):
            try:
                files_data.append((path.name, path.read_bytes()))
            except OSError as e:
                logger.warning(f"Skipped {path}: {e}")
    return files_data


def onboard_tenant(ticker: str, folder: str | None = None) -> int:
    ticker = ticker.upper().strip()
    source_dir = Path(folder) if folder else Path("data/raw") / ticker

    logger.info(f"[{ticker}] Onboarding from {source_dir}")

    if not source_dir.is_dir():
        source_dir.mkdir(parents=True, exist_ok=True)
        logger.error(
            f"[{ticker}] No documents found. Place annual reports, transcripts "
            f"and quarterly results (PDF/CSV) in {source_dir} and re-run."
        )
        return 1

    files_data = collect_documents(source_dir)
    if not files_data:
        logger.error(f"[{ticker}] No PDF/CSV files in {source_dir}. Nothing to ingest.")
        return 1

    logger.info(f"[{ticker}] Found {len(files_data)} document(s). Ingesting into ChromaDB...")

    from rag_engine import ingest_documents, get_collection_stats

    result = ingest_documents(
        ticker,
        files_data,
        progress_callback=lambda msg: logger.info(f"[{ticker}] {msg}"),
    )

    logger.info(
        f"[{ticker}] Ingest complete: {result['total_chunks']} chunks "
        f"(types={result['doc_types']}, collection={result['collection_name']})"
    )
    if result.get("failed_chunks"):
        logger.warning(
            f"[{ticker}] {result['failed_chunks']} chunks failed to embed and were "
            "skipped — re-run once the embedding service recovers."
        )

    stats = get_collection_stats(ticker)
    logger.info(f"[{ticker}] RAG store now holds {stats['total_chunks']} total chunks.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Onboard a new company into the Novus RAG store (ChromaDB)."
    )
    parser.add_argument("ticker", type=str, help="Stock ticker symbol (e.g. SUNPHARMA)")
    parser.add_argument(
        "--folder", type=str, default=None,
        help="Folder containing the company's PDFs/CSVs (default: data/raw/<TICKER>/)",
    )
    args = parser.parse_args()
    sys.exit(onboard_tenant(args.ticker, args.folder))
