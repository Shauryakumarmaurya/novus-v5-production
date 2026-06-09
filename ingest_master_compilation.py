#!/usr/bin/env python3
"""
ingest_master_compilation.py — Ingest the novus_master_compilation.md into ChromaDB.

Parses the pre-chunked 116MB compilation file, extracts metadata per document,
and upserts all segments into the existing ChromaDB collection used by the
RAG pipeline.

Usage:
    python3 ingest_master_compilation.py
    python3 ingest_master_compilation.py --dry-run   # just count, don't embed
    python3 ingest_master_compilation.py --company CIPLA  # ingest one company only
"""

import os
import re
import sys
import logging
import argparse
import hashlib
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── Company name → Screener ticker mapping ──────────────────────────────────

COMPANY_TICKER_MAP = {
    "Alembic Limited": "ALEMBICLTD",
    "Aurobindo Pharma Limited": "AUROPHARMA",
    "Cipla Limited": "CIPLA",
    "Divi's Laboratories Limited": "DIVISLAB",
    "Dr. Reddy's Laboratories Limited": "DRREDDY",
    "Granules India Limited": "GRANULES",
    "Laurus Labs Limited": "LAURUSLABS",
    "Lupin Limited": "LUPIN",
    "Sun Pharmaceutical Industries Limited": "SUNPHARMA",
    "Zydus Lifesciences Limited": "ZYDUSLIFE",
}

# ── Regex patterns ──────────────────────────────────────────────────────────

# Matches: # 1. Alembic Limited
DOC_HEADER_RE = re.compile(r'^# (\d+)\. (.+)$')

# Matches metadata lines like: * **Reporting Period**: FY24
META_RE = re.compile(r'^\* \*\*(.+?)\*\*:\s*(.+)$')

# Matches: ### [Text Block] Segment 1
SEGMENT_RE = re.compile(r'^### \[Text Block\] Segment (\d+)$')


def parse_compilation(filepath: str, company_filter: Optional[str] = None):
    """
    Generator that yields (metadata_dict, segment_num, text) tuples
    from the master compilation file.
    
    Streams the file line-by-line to avoid loading 116MB into memory.
    """
    current_doc = None
    current_meta = {}
    current_segment = None
    current_text_lines = []
    doc_count = 0
    
    with open(filepath, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line_stripped = line.rstrip('\n')
            
            # Check for document header: # N. Company Name
            doc_match = DOC_HEADER_RE.match(line_stripped)
            if doc_match:
                # Yield any buffered segment from previous doc
                if current_segment is not None and current_text_lines:
                    text = '\n'.join(current_text_lines).strip()
                    if text and current_doc:
                        yield current_meta.copy(), current_segment, text
                
                doc_num = int(doc_match.group(1))
                company_name = doc_match.group(2).strip()
                
                # Apply company filter
                if company_filter and company_filter.upper() not in company_name.upper():
                    current_doc = None
                    current_meta = {}
                    current_segment = None
                    current_text_lines = []
                    continue
                
                doc_count += 1
                ticker = COMPANY_TICKER_MAP.get(company_name, company_name.split()[0].upper())
                
                current_doc = doc_num
                current_meta = {
                    "doc_num": doc_num,
                    "company": company_name,
                    "ticker": ticker,
                }
                current_segment = None
                current_text_lines = []
                continue
            
            # Skip if we're filtering and this doc doesn't match
            if current_doc is None:
                continue
            
            # Check for metadata lines
            meta_match = META_RE.match(line_stripped)
            if meta_match:
                key = meta_match.group(1).strip()
                value = meta_match.group(2).strip()
                key_map = {
                    "Reporting Period": "period",
                    "Document Type": "doc_type",
                    "Industry Sector": "sector",
                    "Source File": "source_file",
                    "Page Count": "page_count",
                    "Semantic Chunks": "chunk_count",
                }
                mapped_key = key_map.get(key, key.lower().replace(' ', '_'))
                current_meta[mapped_key] = value
                continue
            
            # Check for segment header
            seg_match = SEGMENT_RE.match(line_stripped)
            if seg_match:
                # Yield any previous segment
                if current_segment is not None and current_text_lines:
                    text = '\n'.join(current_text_lines).strip()
                    if text:
                        yield current_meta.copy(), current_segment, text
                
                current_segment = int(seg_match.group(1))
                current_text_lines = []
                continue
            
            # Skip separator lines
            if line_stripped.startswith('=' * 10) or line_stripped.startswith('-' * 10):
                continue
            
            # Skip anchor tags
            if line_stripped.startswith('<a name='):
                continue
            
            # Accumulate text lines for current segment
            if current_segment is not None:
                current_text_lines.append(line_stripped)
        
        # Yield the final buffered segment
        if current_segment is not None and current_text_lines:
            text = '\n'.join(current_text_lines).strip()
            if text and current_doc:
                yield current_meta.copy(), current_segment, text


def ingest(
    filepath: str,
    dry_run: bool = False,
    company_filter: Optional[str] = None,
    batch_size: int = 100,
):
    """Parse the compilation and ingest into ChromaDB."""
    
    if not dry_run:
        try:
            from rag_engine import get_collection, embed_texts
        except ImportError:
            logger.error("Cannot import rag_engine. Run from the project root.")
            sys.exit(1)
    
    # Counters
    stats = {}
    total_chunks = 0
    batch_ids = []
    batch_texts = []
    batch_metas = []
    
    logger.info(f"Parsing {filepath}...")
    if company_filter:
        logger.info(f"Filtering for company: {company_filter}")
    
    for meta, segment_num, text in parse_compilation(filepath, company_filter):
        ticker = meta.get("ticker", "UNKNOWN")
        doc_type = meta.get("doc_type", "unknown")
        period = meta.get("period", "unknown")
        doc_num = meta.get("doc_num", 0)
        
        # Track stats
        if ticker not in stats:
            stats[ticker] = {"docs": set(), "chunks": 0}
        stats[ticker]["docs"].add(doc_num)
        stats[ticker]["chunks"] += 1
        total_chunks += 1
        
        if dry_run:
            continue
        
        # Build deterministic chunk ID
        chunk_id_str = f"novus_{ticker}_{doc_num}_seg{segment_num}"
        chunk_id = hashlib.md5(chunk_id_str.encode()).hexdigest()
        
        # Build metadata for ChromaDB
        chunk_meta = {
            "ticker": ticker,
            "company": meta.get("company", ""),
            "doc_type": doc_type,
            "period": period,
            "source_file": meta.get("source_file", ""),
            "segment": segment_num,
            "section": doc_type,  # maps to rag_engine's section field
            "source": "novus_master_compilation",
        }
        
        batch_ids.append(chunk_id)
        batch_texts.append(text)
        batch_metas.append(chunk_meta)
        
        # Flush batch
        if len(batch_ids) >= batch_size:
            _flush_batch(ticker, batch_ids, batch_texts, batch_metas)
            batch_ids, batch_texts, batch_metas = [], [], []
    
    # Final flush
    if batch_ids and not dry_run:
        _flush_batch("FINAL", batch_ids, batch_texts, batch_metas)
    
    # Print summary
    logger.info("=" * 60)
    logger.info(f"{'COMPANY':<25} {'DOCS':>6} {'CHUNKS':>8}")
    logger.info("-" * 60)
    for ticker in sorted(stats.keys()):
        s = stats[ticker]
        logger.info(f"{ticker:<25} {len(s['docs']):>6} {s['chunks']:>8}")
    logger.info("-" * 60)
    logger.info(f"{'TOTAL':<25} {sum(len(s['docs']) for s in stats.values()):>6} {total_chunks:>8}")
    logger.info("=" * 60)
    
    if dry_run:
        logger.info("DRY RUN — no data was ingested.")
    else:
        logger.info(f"✅ Ingested {total_chunks} chunks into ChromaDB.")


def _filter_failed_embeddings(ids, texts, metas, embeddings):
    """Drop entries whose embedding failed (None) — never upsert zero/None vectors."""
    kept = [
        (i, t, m, e) for i, t, m, e in zip(ids, texts, metas, embeddings)
        if e is not None
    ]
    skipped = len(ids) - len(kept)
    if skipped:
        logger.error(f"  SKIPPED {skipped} chunks due to embedding failures.")
    if not kept:
        return [], [], [], []
    out_ids, out_texts, out_metas, out_embs = zip(*kept)
    return list(out_ids), list(out_texts), list(out_metas), list(out_embs)


def _flush_batch(label, ids, texts, metas):
    """Embed a batch and upsert into ChromaDB."""
    from rag_engine import embed_texts
    import chromadb
    
    # Get or create collection — use the same one as the main pipeline
    client = chromadb.PersistentClient(path="chroma_db")
    
    # Group by ticker to upsert into per-ticker collections
    ticker_groups = {}
    for i, meta in enumerate(metas):
        t = meta["ticker"]
        if t not in ticker_groups:
            ticker_groups[t] = {"ids": [], "texts": [], "metas": []}
        ticker_groups[t]["ids"].append(ids[i])
        ticker_groups[t]["texts"].append(texts[i])
        ticker_groups[t]["metas"].append(metas[i])
    
    for ticker, group in ticker_groups.items():
        collection_name = f"novus_{ticker.lower()}"
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        
        try:
            embeddings = embed_texts(group["texts"])
            u_ids, u_texts, u_metas, u_embs = _filter_failed_embeddings(
                group["ids"], group["texts"], group["metas"], embeddings
            )
            if u_ids:
                collection.upsert(
                    ids=u_ids,
                    embeddings=u_embs,
                    documents=u_texts,
                    metadatas=u_metas,
                )
            logger.info(f"  [{ticker}] Upserted {len(u_ids)} chunks to {collection_name}")
        except Exception as e:
            logger.error(f"  [{ticker}] Embedding/upsert failed: {e}")
            # Try smaller sub-batches on failure
            sub_batch = 20
            for j in range(0, len(group["texts"]), sub_batch):
                try:
                    sub_texts = group["texts"][j:j+sub_batch]
                    sub_ids = group["ids"][j:j+sub_batch]
                    sub_metas = group["metas"][j:j+sub_batch]
                    sub_embeddings = embed_texts(sub_texts)
                    s_ids, s_texts, s_metas, s_embs = _filter_failed_embeddings(
                        sub_ids, sub_texts, sub_metas, sub_embeddings
                    )
                    if s_ids:
                        collection.upsert(
                            ids=s_ids,
                            embeddings=s_embs,
                            documents=s_texts,
                            metadatas=s_metas,
                        )
                    logger.info(f"  [{ticker}] Sub-batch {j//sub_batch + 1} OK")
                    time.sleep(0.5)
                except Exception as e2:
                    logger.error(f"  [{ticker}] Sub-batch {j//sub_batch + 1} failed: {e2}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Ingest novus_master_compilation.md into ChromaDB")
    ap.add_argument("--file", default="novus_master_compilation.md",
                    help="Path to the master compilation file")
    ap.add_argument("--dry-run", action="store_true",
                    help="Only parse and count; don't embed or upsert")
    ap.add_argument("--company", type=str, default=None,
                    help="Filter to a single company name (e.g., 'Cipla')")
    ap.add_argument("--batch-size", type=int, default=100,
                    help="Embedding batch size (default: 100)")
    args = ap.parse_args()
    
    if not os.path.exists(args.file):
        logger.error(f"File not found: {args.file}")
        sys.exit(1)
    
    ingest(
        filepath=args.file,
        dry_run=args.dry_run,
        company_filter=args.company,
        batch_size=args.batch_size,
    )
