# rag_engine.py
"""
Novus RAG Engine — Retrieval Augmented Generation over Full Company Datasets

Supports: Annual Reports, Investor Presentations, Quarterly Results,
Credit Rating Reports, Concall Transcripts, Research Reports

Stack:
  Parser: PyMuPDF (text)
  Embeddings: Voyage AI voyage-finance-2
  Vector Store: ChromaDB (local, persistent)
  Chunking: Section-aware, 1000 tokens, 200 overlap
"""

from utils.logger import get_logger
logger = get_logger(__name__)
import os
import re
import json
import csv
import io
import hashlib
import fitz  # PyMuPDF
from typing import Optional
from dotenv import load_dotenv

import chromadb
from chromadb.config import Settings
# google.generativeai removed — embeddings use Voyage AI, critic uses DeepSeek via LLMClient

# Prevent ChromaDB from implicitly loading PyTorch + MiniLM on macOS
import chromadb.utils.embedding_functions
class DummyDefaultEmbeddingFunction(chromadb.EmbeddingFunction):
    def __call__(self, input: chromadb.Documents) -> chromadb.Embeddings:
        return [[0.0]*1024 for _ in input]
chromadb.utils.embedding_functions.DefaultEmbeddingFunction = DummyDefaultEmbeddingFunction

import voyageai
load_dotenv()
raw_voyage_key = os.getenv("VOYAGE_API_KEY", "")
clean_voyage_key = raw_voyage_key.strip('"\'')
voyage_client = voyageai.Client(api_key=clean_voyage_key)

# ── Constants ────────────────────────────────────────────────────────────────

CHROMA_PERSIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
EMBEDDING_MODEL = "voyage-finance-2"

# Doc-type aware chunk sizing:
#   Annual reports / financial data → 8K chars for richer table & disclosure context
#   Transcripts / other → 4K chars for Q&A granularity
CHUNK_DEFAULTS = {
    "annual_report":    {"chunk_size": 8000, "overlap": 1600},
    "financial_data":   {"chunk_size": 8000, "overlap": 1600},
    "credit_report":    {"chunk_size": 8000, "overlap": 1600},
    "_default":         {"chunk_size": 4000, "overlap": 800},
}

# Legacy constants for backward compatibility
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
MAX_CHAR_PER_CHUNK = CHUNK_SIZE * 4
OVERLAP_CHARS = CHUNK_OVERLAP * 4


# ── Document Type Classification ─────────────────────────────────────────────

DOC_TYPE_PATTERNS = {
    "annual_report": [
        r"(?i)annual\s*report",
        r"(?i)director'?s?\s*report",
        r"(?i)board'?s?\s*report",
        r"(?i)balance\s*sheet\s*as\s*at",
        r"(?i)notes\s*to\s*(?:the\s*)?financial\s*statements",
        r"(?i)auditor'?s?\s*report",
    ],
    "concall_transcript": [
        r"(?i)(?:con\s*call|conference\s*call|earnings\s*call)",
        r"(?i)(?:q[1-4]\s*(?:fy|20))",
        r"(?i)(?:transcript|q\s*&\s*a\s*session)",
        r"(?i)(?:management|moderator)\s*:\s*",
    ],
    "investor_presentation": [
        r"(?i)investor\s*(?:presentation|deck|update)",
        r"(?i)(?:corporate|business)\s*(?:presentation|overview)",
        r"(?i)capital\s*markets?\s*day",
    ],
    "quarterly_results": [
        r"(?i)(?:quarterly|q[1-4])\s*result",
        r"(?i)financial\s*results?\s*for\s*(?:the\s*)?(?:quarter|period)",
        r"(?i)unaudited\s*financial\s*results?",
    ],
    "credit_report": [
        r"(?i)(?:credit|rating)\s*(?:report|rationale|action)",
        r"(?i)(?:crisil|icra|care|india\s*ratings|fitch|moody)",
    ],
    "research_report": [
        r"(?i)(?:equity|stock|sector)\s*research",
        r"(?i)(?:buy|sell|hold|outperform|underperform)\s*(?:rating|target)",
        r"(?i)target\s*price",
    ],
}


def classify_document_type(text: str) -> str:
    """Classify document type based on content patterns."""
    text_sample = text[:5000]  # Check first 5000 chars
    scores = {}

    for doc_type, patterns in DOC_TYPE_PATTERNS.items():
        score = sum(1 for p in patterns if re.search(p, text_sample))
        scores[doc_type] = score

    best_type = max(scores, key=scores.get)
    return best_type if scores[best_type] > 0 else "other"


# ── Section Detection ─────────────────────────────────────────────────────────

SECTION_PATTERNS = [
    # Common annual report sections
    r"(?i)^(?:#{1,3}\s+)?(director'?s?\s*report)",
    r"(?i)^(?:#{1,3}\s+)?(management\s*discussion\s*(?:and|&)\s*analysis)",
    r"(?i)^(?:#{1,3}\s+)?(notes\s*to\s*(?:the\s*)?financial\s*statements?)",
    r"(?i)^(?:#{1,3}\s+)?(corporate\s*governance)",
    r"(?i)^(?:#{1,3}\s+)?(auditor'?s?\s*report)",
    r"(?i)^(?:#{1,3}\s+)?(risk\s*management)",
    r"(?i)^(?:#{1,3}\s+)?(related\s*party\s*transactions?)",
    r"(?i)^(?:#{1,3}\s+)?(contingent\s*liabilities?)",
    # Concall sections
    r"(?i)^(?:#{1,3}\s+)?(opening\s*(?:statement|remarks?))",
    r"(?i)^(?:#{1,3}\s+)?(question\s*(?:and|&)\s*answer|q\s*&\s*a)",
    r"(?i)^(?:#{1,3}\s+)?(closing\s*(?:statement|remarks?))",
]


def detect_sections(text: str) -> list[dict]:
    """
    Detect logical sections in a document.
    Returns list of {title, start_idx, end_idx}.
    """
    sections = []
    lines = text.split('\n')
    current_section = {"title": "Introduction", "start_idx": 0, "end_idx": 0}

    char_offset = 0
    for line in lines:
        for pattern in SECTION_PATTERNS:
            match = re.match(pattern, line.strip())
            if match:
                # Close previous section
                current_section["end_idx"] = char_offset
                if current_section["end_idx"] > current_section["start_idx"]:
                    sections.append(current_section.copy())

                # Start new section
                current_section = {
                    "title": match.group(1).strip() if match.group(1) else line.strip(),
                    "start_idx": char_offset,
                    "end_idx": 0,
                }
                break

        char_offset += len(line) + 1  # +1 for newline

    # Close last section
    current_section["end_idx"] = len(text)
    if current_section["end_idx"] > current_section["start_idx"]:
        sections.append(current_section)

    # If no sections detected, treat entire document as one section
    if not sections:
        sections = [{"title": "Full Document", "start_idx": 0, "end_idx": len(text)}]

    return sections


# ── Chunking ──────────────────────────────────────────────────────────────────

import re as _re_chunk  # local import to avoid shadowing

_TABLE_LINE_RE = _re_chunk.compile(
    r'^[\s]*'
    r'(?:'
    r'[|]'                                   # markdown table rows
    r'|[\w\s,.()]+\t'                        # tab-separated data
    r'|[\w\s,.()]+\s{3,}[\d,.()%-]+'         # space-aligned numeric columns
    r'|---'                                   # markdown separator
    r')',
    _re_chunk.MULTILINE,
)


def _detect_atomic_blocks(text: str) -> list[tuple[int, int]]:
    """
    Find contiguous table-like blocks that must NOT be split mid-row.
    Returns list of (start_char, end_char) spans.
    """
    lines = text.split('\n')
    blocks = []
    block_start = None
    char_pos = 0

    for i, line in enumerate(lines):
        is_table = bool(_TABLE_LINE_RE.match(line))
        if is_table:
            if block_start is None:
                block_start = char_pos
        else:
            if block_start is not None:
                # End of a table block — need at least 2 table lines to count
                blocks.append((block_start, char_pos))
                block_start = None
        char_pos += len(line) + 1  # +1 for \n

    if block_start is not None:
        blocks.append((block_start, char_pos))

    # Filter out tiny blocks (< 2 lines)
    return [(s, e) for s, e in blocks if text[s:e].count('\n') >= 2]


def chunk_text(
    text: str,
    chunk_size: int = MAX_CHAR_PER_CHUNK,
    overlap: int = OVERLAP_CHARS,
) -> list[str]:
    """
    Split text into chunks with overlap.
    Table-aware: detects contiguous table blocks and keeps them atomic.
    Tries to split at sentence boundaries when possible.
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    # Pre-detect atomic table blocks
    atomic_blocks = _detect_atomic_blocks(text)

    def _is_inside_table(pos: int) -> tuple[bool, int]:
        """Check if position is inside an atomic block. Returns (inside, block_end)."""
        for bs, be in atomic_blocks:
            if bs <= pos < be:
                return True, be
        return False, pos

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # If the proposed split point lands inside a table, extend to table end
        inside, block_end = _is_inside_table(end)
        if inside:
            end = min(block_end, len(text))
        elif end < len(text):
            # Try to find a sentence boundary near the end (last 20%)
            search_start = end - int(chunk_size * 0.2)
            search_region = text[search_start:end]

            for sep in ['. ', '.\n', ';\n', '\n\n']:
                last_sep = search_region.rfind(sep)
                if last_sep != -1:
                    # Make sure this boundary isn't inside a table
                    candidate = search_start + last_sep + len(sep)
                    in_tbl, _ = _is_inside_table(candidate)
                    if not in_tbl:
                        end = candidate
                        break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap

    return chunks


# ═══════════════════════════════════════════════════════════════════════════
# Fiscal-period extraction (strict, multi-source, temporal-bleed prevention)
# ═══════════════════════════════════════════════════════════════════════════

_INDIAN_FY_QUARTER_BY_MONTH = {
    # Indian fiscal year runs Apr -> Mar. Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar.
    1: "Q4", 2: "Q4", 3: "Q4",
    4: "Q1", 5: "Q1", 6: "Q1",
    7: "Q2", 8: "Q2", 9: "Q2",
    10: "Q3", 11: "Q3", 12: "Q3",
}

_MONTH_NAME_TO_NUM = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _fiscal_period_from_month_year(calendar_month: int, calendar_year: int) -> tuple[str, str, str]:
    """Return (fiscal_year, fiscal_quarter, fiscal_period) for a calendar date.

    Example: Dec 2025 -> ("FY26", "Q3", "Q3_FY26")
             Mar 2024 -> ("FY24", "Q4", "Q4_FY24")
             Apr 2024 -> ("FY25", "Q1", "Q1_FY25")
    """
    fy_year = calendar_year + 1 if calendar_month >= 4 else calendar_year
    fy_yy = fy_year % 100
    fiscal_year = f"FY{fy_yy:02d}"
    fiscal_quarter = _INDIAN_FY_QUARTER_BY_MONTH.get(calendar_month, "")
    fiscal_period = f"{fiscal_quarter}_{fiscal_year}" if fiscal_quarter else fiscal_year
    return fiscal_year, fiscal_quarter, fiscal_period


def extract_fiscal_period(filename: str = "", content_head: str = "") -> dict:
    """Best-effort inference of fiscal metadata for a document.

    Detection order (most reliable first):
      1. Explicit "quarter ended <Month> <Day>, <Year>" in document head (concalls / results press releases)
      2. Explicit "year ended March 31, <Year>" in document head (annual reports)
      3. Filename pattern like 'Q3_FY26', 'Q3FY2026', 'Q3_2022'
      4. Filename pattern like 'FY24', 'FY2024', 'Financial_Year_2024'
      5. Filename pattern like 'Mar_2025', 'December 2025'
      6. Bare 4-digit year in filename (last resort, logged as low-confidence)

    Returns a dict with:
      - fiscal_year     ("FY26" | "")
      - fiscal_quarter  ("Q3"   | "")
      - fiscal_period   ("Q3_FY26" | "FY26" | "UNKNOWN")
      - calendar_year   (int)                — for legacy back-compat
      - calendar_month  (int | None)         — month if determinable
      - detection_source (str)               — which rule fired, for debugging
      - confidence      ("high" | "medium" | "low")
    """
    fname = filename or ""
    head = (content_head or "")[:3000].replace("\xa0", " ")

    # ── Rule 1: content — "quarter ended <Month> <day>, <year>" ──
    m = re.search(
        r"(?i)(?:quarter|period)\s+ended\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+\d{1,2}\s*,?\s*(\d{4})",
        head,
    )
    if m:
        month = _MONTH_NAME_TO_NUM.get(m.group(1).lower()[:3])
        cal_year = int(m.group(2))
        if month:
            fy, fq, fp = _fiscal_period_from_month_year(month, cal_year)
            return {
                "fiscal_year": fy, "fiscal_quarter": fq, "fiscal_period": fp,
                "calendar_year": cal_year, "calendar_month": month,
                "detection_source": "content_quarter_ended",
                "confidence": "high",
            }

    # ── Rule 2: content — "year ended March 31, <year>" (annual reports) ──
    m = re.search(
        r"(?i)(?:year|fiscal\s+year|financial\s+year)\s+ended\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+\d{1,2}\s*,?\s*(\d{4})",
        head,
    )
    if m:
        month = _MONTH_NAME_TO_NUM.get(m.group(1).lower()[:3])
        cal_year = int(m.group(2))
        if month:
            fy, _fq, _fp = _fiscal_period_from_month_year(month, cal_year)
            # Annual report: no quarter
            return {
                "fiscal_year": fy, "fiscal_quarter": "", "fiscal_period": fy,
                "calendar_year": cal_year, "calendar_month": month,
                "detection_source": "content_year_ended",
                "confidence": "high",
            }

    # ── Rule 3: filename — 'Q3_FY26', 'Q3FY2026', 'Q3 FY 2025-26' ──
    m = re.search(r"(?i)Q([1-4])\s*[_\s-]*FY\s*(\d{2,4})(?:\s*[-/]\s*\d{2,4})?", fname)
    if m:
        q = int(m.group(1))
        fy_raw = m.group(2)
        fy_year = int(fy_raw) if len(fy_raw) == 4 else 2000 + int(fy_raw)
        fy_yy = fy_year % 100
        fiscal_year = f"FY{fy_yy:02d}"
        fiscal_quarter = f"Q{q}"
        # Back-derive calendar year for back-compat
        q_end_month = {1: 6, 2: 9, 3: 12, 4: 3}[q]
        cal_year = fy_year - 1 if q_end_month >= 4 else fy_year
        return {
            "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter,
            "fiscal_period": f"{fiscal_quarter}_{fiscal_year}",
            "calendar_year": cal_year, "calendar_month": q_end_month,
            "detection_source": "filename_q_fy",
            "confidence": "high",
        }

    # ── Rule 4: filename — 'FY24', 'FY2024', 'Financial_Year_2024' (annual) ──
    m = re.search(r"(?i)(?:FY|Financial[_\s]Year)[_\s]*(\d{2,4})", fname)
    if m:
        fy_raw = m.group(1)
        fy_year = int(fy_raw) if len(fy_raw) == 4 else 2000 + int(fy_raw)
        fy_yy = fy_year % 100
        fiscal_year = f"FY{fy_yy:02d}"
        return {
            "fiscal_year": fiscal_year, "fiscal_quarter": "",
            "fiscal_period": fiscal_year,
            "calendar_year": fy_year - 1, "calendar_month": 3,
            "detection_source": "filename_fy",
            "confidence": "high",
        }

    # ── Rule 5: filename — 'Q3_2022' (ambiguous — treat year as calendar year of the reporting quarter) ──
    m = re.search(r"(?i)Q([1-4])[_\s-]+(\d{4})", fname)
    if m:
        q = int(m.group(1))
        cal_year = int(m.group(2))
        # Many Indian concall filenames use 'Q3_2022' meaning 'Q3 of calendar year
        # 2022' (quarter ended Dec 2022). Content scan is the authoritative source,
        # so this rule deliberately has medium confidence only.
        q_end_month = {1: 3, 2: 6, 3: 9, 4: 12}[q]
        # Wait — in Indian fiscal convention this is ambiguous: Q3_2022 could mean
        # either (a) calendar Q3 2022 = Jul-Sep 2022 or (b) fiscal Q3 FY22 = Oct-Dec 2021.
        # The only honest move is to use calendar-quarter semantics here and
        # let the content_head rule (#1) override when available. Most Indian
        # concall PDFs DO have "quarter ended" in the first page, so rule 1 wins.
        cal_month = {1: 3, 2: 6, 3: 9, 4: 12}[q]   # calendar-quarter end month
        fy, fq, fp = _fiscal_period_from_month_year(cal_month, cal_year)
        return {
            "fiscal_year": fy, "fiscal_quarter": fq, "fiscal_period": fp,
            "calendar_year": cal_year, "calendar_month": cal_month,
            "detection_source": "filename_q_calyear",
            "confidence": "medium",
        }

    # ── Rule 6: filename — 'Mar_2025', 'December 2025' ──
    m = re.search(
        r"(?i)(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"[_\s-]*(\d{4})",
        fname,
    )
    if m:
        month = _MONTH_NAME_TO_NUM.get(m.group(1).lower()[:3])
        cal_year = int(m.group(2))
        if month:
            fy, fq, fp = _fiscal_period_from_month_year(month, cal_year)
            return {
                "fiscal_year": fy, "fiscal_quarter": fq, "fiscal_period": fp,
                "calendar_year": cal_year, "calendar_month": month,
                "detection_source": "filename_month_year",
                "confidence": "medium",
            }

    # ── Rule 7: bare year in filename — last resort ──
    m = re.search(r"(19|20)\d{2}", fname)
    if m:
        cal_year = int(m.group(0))
        # No month signal — assume annual, fiscal year = year if filename
        # looks like an Annual Report, else best-guess.
        fy, _fq, _fp = _fiscal_period_from_month_year(3, cal_year)  # treat as March
        return {
            "fiscal_year": fy, "fiscal_quarter": "",
            "fiscal_period": fy,
            "calendar_year": cal_year, "calendar_month": 3,
            "detection_source": "filename_bare_year",
            "confidence": "low",
        }

    # ── Total failure — log loudly ──
    logger.warning(
        f"[RAG] Could not infer fiscal period from filename={filename!r} or "
        f"first 3K of content. Chunks will be tagged UNKNOWN and will be "
        f"excluded from time-filtered queries."
    )
    return {
        "fiscal_year": "", "fiscal_quarter": "",
        "fiscal_period": "UNKNOWN",
        "calendar_year": 0, "calendar_month": None,
        "detection_source": "fallback_none",
        "confidence": "none",
    }


def chunk_document_with_sections(
    text: str,
    doc_type: str,
    ticker: str,
    filename: str,
    page_map: dict = None,
) -> list[dict]:
    """
    Chunk a document with section awareness and rich metadata.
    Each chunk gets: {text, metadata: {ticker, doc_type, section, filename, chunk_id,
                     fiscal_year, fiscal_quarter, fiscal_period, year, quarter}}

    Fiscal metadata is extracted once per document via extract_fiscal_period(),
    which inspects BOTH the filename and the first 3000 characters of the
    document. This prevents the 'Temporal Bleed' bug where filename-only
    inference silently mis-tagged half the RAG store as year 2020.
    """
    sections = detect_sections(text)
    all_chunks = []
    chunk_counter = 0

    # Robust fiscal-period inference — single source of truth per document.
    fp_info = extract_fiscal_period(filename=filename, content_head=text[:3000])
    fiscal_year = fp_info["fiscal_year"]
    fiscal_quarter = fp_info["fiscal_quarter"]
    fiscal_period = fp_info["fiscal_period"]
    doc_year = fp_info["calendar_year"] or 0

    if fp_info["confidence"] in ("low", "none"):
        logger.warning(
            f"[RAG] LOW-CONFIDENCE fiscal period for {filename!r}: "
            f"period={fiscal_period!r} source={fp_info['detection_source']}. "
            "Downstream time-filtered queries may miss this document."
        )
    else:
        logger.info(
            f"[RAG] Fiscal metadata for {filename!r}: "
            f"period={fiscal_period!r} via {fp_info['detection_source']}"
        )

    # Legacy-compat fields — many downstream consumers still read `quarter` / `year`.
    quarter = fiscal_quarter  # e.g. "Q3" or ""

    # Timeline prefix baked into the chunk text itself so the LLM sees the period
    # even when it doesn't explicitly inspect metadata.
    doc_tag = doc_type.replace('_', ' ').title()
    q_tag = f" | Quarter: {fiscal_quarter}" if fiscal_quarter else ""
    timeline_prefix = (
        f"[Fiscal Period: {fiscal_period}{q_tag} | "
        f"Calendar Year: {doc_year} | Document: {doc_tag}]\n"
    )

    # Determine chunk sizing based on document type
    sizing = CHUNK_DEFAULTS.get(doc_type, CHUNK_DEFAULTS["_default"])
    effective_chunk_size = sizing["chunk_size"]
    effective_overlap = sizing["overlap"]

    for section in sections:
        section_text = text[section["start_idx"]:section["end_idx"]]
        if not section_text.strip():
            continue

        text_chunks = chunk_text(section_text, effective_chunk_size, effective_overlap)

        for i, chunk in enumerate(text_chunks):
            chunk_counter += 1
            chunk_id = hashlib.md5(
                f"{ticker}_{filename}_{section['title']}_{i}_{chunk_counter}_{chunk[:50]}".encode()
            ).hexdigest()

            # Prepend temporal metadata
            injected_text = f"{timeline_prefix}{chunk}"

            all_chunks.append({
                "id": chunk_id,
                "text": injected_text,
                "metadata": {
                    "ticker": ticker.upper(),
                    "doc_type": doc_type,
                    "section": section["title"],
                    "filename": filename,
                    # New strict fiscal fields (per architecture.md contract)
                    "fiscal_year": fiscal_year,
                    "fiscal_quarter": fiscal_quarter,
                    "fiscal_period": fiscal_period,
                    "fiscal_detection_source": fp_info["detection_source"],
                    "fiscal_confidence": fp_info["confidence"],
                    # Legacy fields preserved for back-compat
                    "year": doc_year,
                    "quarter": quarter,
                    "chunk_index": i,
                    "total_section_chunks": len(text_chunks),
                    "char_count": len(chunk),
                },
            })

    return all_chunks


# ── Embedding ─────────────────────────────────────────────────────────────────

import time

def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings using Voyage AI Finance model with robust rate-limit handling.
    """
    embeddings = []

    # Voyage AI enforces 120K tokens per batch. With 8K-char chunks (annual reports),
    # 120 items can exceed 240K tokens. Use batch_size=40 to stay safely under the limit.
    batch_size = 40
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        
        max_retries = 5
        for attempt in range(max_retries):
            try:
                result = voyage_client.embed(
                    batch,
                    model=EMBEDDING_MODEL,
                    input_type="document",
                    truncation=True
                )
                embeddings.extend(result.embeddings)
                break # Success, break out of retry loop
                
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RateLimit" in err_str or "Quota" in err_str:
                    sleep_time = 15 * (attempt + 1)
                    logger.warning(f"[RAG] Rate limit hit on batch {i}. Retrying in {sleep_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(sleep_time)
                else:
                    logger.error(f"[RAG] Final Embedding error for batch {i}: {e}")
                    # Only fallback if completely unrecoverable non-rate-limit error
                    embeddings.extend([[0.0] * 1024] * len(batch))
                    break
        else:
            # If we exhausted all 5 retries due to persistent rate limiting
            logger.error(f"[RAG] Exhausted all retries for batch {i}. Injecting zero vectors.")
            embeddings.extend([[0.0] * 1024] * len(batch))

    return embeddings


def embed_query(query: str) -> list[float]:
    """Embed a single query string for retrieval using Voyage AI."""
    try:
        result = voyage_client.embed(
            [query],
            model=EMBEDDING_MODEL,
            input_type="query",
            truncation=True
        )
        return result.embeddings[0]
    except Exception as e:
        logger.error(f"[RAG] Query embedding error: {e}")
        return [0.0] * 1024


# ── ChromaDB Management ──────────────────────────────────────────────────────

_chroma_client = None

def get_chroma_client() -> chromadb.ClientAPI:
    """Get or create a persistent ChromaDB client as a singleton."""
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    return _chroma_client


def get_collection(ticker: str) -> chromadb.Collection:
    """Get or create a ChromaDB collection for a specific ticker."""
    client = get_chroma_client()
    
    base_name = ticker.replace('-', '_').replace('&', '_')
    upper_name = f"novus_{base_name.upper()}"
    lower_name = f"novus_{base_name.lower()}"
    
    # Try lowercase first (from bulk ingestion), fallback to uppercase (legacy)
    existing = [c.name for c in client.list_collections()]
    collection_name = lower_name if lower_name in existing else upper_name

    # Ensure collection name is valid (alphanumeric + underscore, 3-63 chars)
    collection_name = re.sub(r'[^a-zA-Z0-9_]', '_', collection_name)[:63]
    if len(collection_name) < 3:
        collection_name = f"novus_{collection_name}_docs"

    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )


# ── Core API ──────────────────────────────────────────────────────────────────

def ingest_documents(
    ticker: str,
    files_data: list[tuple[str, bytes]],  # [(filename, bytes), ...]
    progress_callback=None,
) -> dict:
    """
    Parse, chunk, embed, and store documents in ChromaDB.
    
    Returns: {total_chunks, doc_types, sections_found}
    """
    collection = get_collection(ticker)
    total_chunks = 0
    doc_types_found = set()
    all_sections = set()

    for idx, (filename, file_bytes) in enumerate(files_data):
        if progress_callback:
            progress_callback(f"Processing {filename} ({idx+1}/{len(files_data)})")

        ext = os.path.splitext(filename)[1].lower()

        # --- CSV / Structured Data Ingestion ---
        if ext == '.csv':
            try:
                csv_text = file_bytes.decode('utf-8', errors='replace')
                reader = csv.reader(io.StringIO(csv_text))
                rows = list(reader)
                if not rows:
                    logger.info(f"[RAG] Empty CSV: {filename}")
                    continue
                # Convert CSV to a readable markdown table string
                header = rows[0]
                data_label = os.path.splitext(filename)[0].replace('_', ' ').title()
                md_lines = [f"## {data_label} — Financial Data Table\n"]
                md_lines.append("| " + " | ".join(header) + " |")
                md_lines.append("| " + " | ".join(['---'] * len(header)) + " |")
                for row in rows[1:]:
                    if any(cell.strip() for cell in row):
                        md_lines.append("| " + " | ".join(row) + " |")
                text = "\n".join(md_lines)
                doc_type = "financial_data"
            except Exception as e:
                logger.info(f"[RAG] Failed to parse CSV {filename}: {e}")
                continue
        else:
            # --- PDF Ingestion ---
            try:
                # Quick text extraction first (needed for doc_type classification)
                pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
                quick_text = ""
                for page in pdf_doc:
                    quick_text += page.get_text()
                pdf_doc.close()
            except Exception as e:
                logger.info(f"[RAG] Failed to parse {filename}: {e}")
                continue

            if not quick_text.strip():
                logger.info(f"[RAG] No text extracted from {filename}")
                continue

            # Classify document type from the quick text pass
            doc_type = classify_document_type(quick_text)
            
            # STRICT GATEKEEPER: Drop non-financial files immediately
            if doc_type == "other":
                logger.warning(f"[RAG] REJECTED {filename}: Unrecognized document type.")
                continue 

            # ── Hybrid Vision Parsing for table-heavy documents ──
            # Annual reports and quarterly results have financial tables that
            # get destroyed by standard text extraction. Use VisionParser for
            # those pages while keeping fast text extraction for narrative pages.
            VISION_ELIGIBLE_TYPES = {"annual_report", "quarterly_results", "credit_report"}

            if doc_type in VISION_ELIGIBLE_TYPES:
                try:
                    from vision_parser import parse_pdf_bytes
                    if progress_callback:
                        progress_callback(f"Vision parsing {filename} (table pages only)...")
                    text = parse_pdf_bytes(
                        file_bytes,
                        filename,
                        hybrid=True,
                        progress_callback=progress_callback,
                    )
                    logger.info(f"[RAG] Hybrid vision parse complete for {filename}")
                except Exception as e:
                    logger.warning(
                        f"[RAG] Vision parser failed for {filename}: {e}. "
                        "Falling back to standard text extraction."
                    )
                    text = quick_text
            else:
                # Concall transcripts, research reports, etc. — text extraction is fine
                text = quick_text
            
        doc_types_found.add(doc_type)

        # Chunk with section awareness
        chunks = chunk_document_with_sections(
            text, doc_type, ticker, filename
        )

        if not chunks:
            continue

        # Filter out empty/whitespace-only chunks
        chunks = [c for c in chunks if c["text"].strip()]
        if not chunks:
            continue

        # Track sections
        for c in chunks:
            all_sections.add(c["metadata"]["section"])

        # Generate embeddings
        texts = [c["text"] for c in chunks]
        embeddings = embed_texts(texts)

        # Store in ChromaDB
        collection.upsert(
            ids=[c["id"] for c in chunks],
            embeddings=embeddings,
            documents=texts,
            metadatas=[c["metadata"] for c in chunks],
        )

        total_chunks += len(chunks)
        logger.info(f"[RAG] Ingested {filename}: {len(chunks)} chunks, type={doc_type}")

    return {
        "total_chunks": total_chunks,
        "doc_types": list(doc_types_found),
        "sections_found": list(all_sections),
        "collection_name": collection.name,
    }


def query(
    ticker: str,
    question: str,
    top_k: int = 5,
    doc_type_filter: str | list[str] = None,
    section_filter: str = None,
    min_year: int = None,
    metadata_filters: dict = None,
    target_fiscal_period: str | list[str] = None,
    target_fiscal_year: str | list[str] = None,
    target_fiscal_quarter: str | list[str] = None,
) -> list[dict]:
    """
    Query the RAG store for relevant chunks.

    Returns list of {text, metadata, distance, chunk_id} sorted by relevance.

    Temporal filtering (strongly encouraged for all agent queries):
      target_fiscal_period: e.g. "Q3_FY26" or ["Q3_FY26", "Q2_FY26"]
      target_fiscal_year:   e.g. "FY26" or ["FY25", "FY26"]

    Passing either narrows the vector search to chunks tagged with matching
    fiscal metadata BEFORE embedding similarity is computed. This is the
    primary defense against 'Temporal Bleed' hallucinations where the model
    stitches a Q2 FY25 event onto a Q3 FY26 timeline just because the
    semantic content is similar.

    min_year is retained for back-compat but the legacy `year` int field is
    known to be unreliable for pre-existing chunks ingested before the
    fiscal metadata refactor. Prefer target_fiscal_period.
    """
    collection = get_collection(ticker)

    # Build metadata filter
    where_filter = {}
    if doc_type_filter:
        if isinstance(doc_type_filter, list) and len(doc_type_filter) > 0:
            where_filter["doc_type"] = {"$in": doc_type_filter}
        elif isinstance(doc_type_filter, str):
            where_filter["doc_type"] = doc_type_filter
    if section_filter:
        where_filter["section"] = section_filter

    # New temporal filters — preferred over min_year
    if target_fiscal_period:
        if isinstance(target_fiscal_period, list) and target_fiscal_period:
            where_filter["fiscal_period"] = {"$in": target_fiscal_period}
        elif isinstance(target_fiscal_period, str):
            where_filter["fiscal_period"] = target_fiscal_period
    if target_fiscal_year:
        if isinstance(target_fiscal_year, list) and target_fiscal_year:
            where_filter["fiscal_year"] = {"$in": target_fiscal_year}
        elif isinstance(target_fiscal_year, str):
            where_filter["fiscal_year"] = target_fiscal_year
    if target_fiscal_quarter:
        if isinstance(target_fiscal_quarter, list) and target_fiscal_quarter:
            where_filter["fiscal_quarter"] = {"$in": target_fiscal_quarter}
        elif isinstance(target_fiscal_quarter, str):
            where_filter["fiscal_quarter"] = target_fiscal_quarter

    if metadata_filters:
        where_filter.update(metadata_filters)

    if min_year:
        where_clause = {"$and": [{"ticker": ticker.upper()}, {"year": {"$gte": min_year}}]}
        for k, v in where_filter.items():
            where_clause["$and"].append({k: v})
        final_where = where_clause
    else:
        # ChromaDB requires $and when combining $in with other filters
        if where_filter:
            conditions = [{"ticker": ticker.upper()}]
            for k, v in where_filter.items():
                conditions.append({k: v})
            final_where = {"$and": conditions} if len(conditions) > 1 else conditions[0]
        else:
            final_where = {"ticker": ticker.upper()}

    # Embed query
    query_embedding = embed_query(question)

    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, collection.count() or 1),
            where=final_where,
        )
    except Exception as e:
        # FAIL LOUDLY. Do not fallback and pollute the context.
        logger.error(f"[RAG] CRITICAL: Query failed with filter {final_where}. Error: {e}")
        return [{"text": "Data Unavailable for this specific fiscal period.", "metadata": {}, "chunk_id": None, "relevance": 0.0}]

    # Format results
    formatted = []
    if results and results.get("documents") and results["documents"][0]:
        docs = results["documents"][0]
        metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
        dists = results["distances"][0] if results.get("distances") else [0.0] * len(docs)
        ids = results["ids"][0] if results.get("ids") else [None] * len(docs)

        for doc, meta, dist, cid in zip(docs, metas, dists, ids):
            formatted.append({
                "text": doc,
                "metadata": meta,
                "chunk_id": cid,   # ChromaDB chunk id — used for Hover-to-Verify provenance
                "relevance": round(1 - dist, 3),  # Convert distance to similarity
            })
    else:
        return [{"text": "Data Unavailable for this specific fiscal period.", "metadata": {}, "chunk_id": None, "relevance": 0.0}]

    return formatted


def get_context_for_agent(
    ticker: str,
    agent_name: str,
) -> str:
    """
    Pre-built RAG queries tailored for each agent.
    Returns a combined context string from retrieved chunks.
    """
    collection = get_collection(ticker)

    # Check if we have any documents ingested
    if collection.count() == 0:
        return ""  # No RAG context available

    agent_queries = {
        "forensic_quant": [
            "contingent liabilities off balance sheet guarantees",
            "capital expenditure capex investment plans",
            "working capital receivables payables inventory",
            "debt repayment schedule maturity profile",
        ],
        "forensic_investigator": [
            "auditor change appointment resignation",
            "related party transactions subsidiary loans",
            "revenue recognition policies accounting changes",
            "inventory write-downs impairment charges",
            "regulatory actions tax disputes litigation"
        ],
        "narrative_decoder": [
            "management guidance outlook vision strategy",
            "industry headwinds tailwinds market size growth",
            "product launches pricing strategy margins",
            "supply chain disruptions material costs"
        ],
        "moat_architect": [
            "competitive advantage moat market position",
            "market share brand equity distribution network",
            "barriers to entry switching costs",
            "customer concentration dependency pricing power"
        ],
        "capital_allocator": [
            "capital allocation dividend buyback returns",
            "return on invested capital roic wc trends",
            "free cash flow generation conversion",
            "acquisitions m&a strategy divestments"
        ],
        "management_quality": [
            "promoter pledge shares holding percentage",
            "board independence promoter remuneration",
            "equity dilution allotment shares esops",
            "succession planning governance issues"
        ],
        "pm_synthesis": [
            "key risks uncertainties concerns",
            "growth drivers opportunity pipeline order book",
            "catalysts re-rating downgrades",
            "capital allocation strategy execution"
        ],
        "critic_agent": [
            "auditor change appointment statutory audit",
            "revenue recognition accounting policy changes",
            "related party transactions intercompany balances",
            "contingent liabilities legal disputes provisions",
            "promoter shareholding pledge encumbrance"
        ],
    }

    queries = agent_queries.get(agent_name, ["company overview business model"])

    all_chunks = []
    seen_ids = set()

    for q in queries:
        results = query(ticker, q, top_k=3)
        for r in results:
            # Hash the FULL text to avoid collisions from temporal metadata prefix
            chunk_id = hashlib.md5(r["text"].encode()).hexdigest()
            if chunk_id not in seen_ids:
                seen_ids.add(chunk_id)
                all_chunks.append(r)

    if not all_chunks:
        return ""

    # Build context string with source citations
    context_parts = []
    context_parts.append("--- RAG CONTEXT (from uploaded company documents) ---\n")

    for i, chunk in enumerate(all_chunks[:12], 1):  # Max 12 chunks
        meta = chunk.get("metadata", {})
        source = f"[Source: {meta.get('filename', '?')} | {meta.get('doc_type', '?')} | Section: {meta.get('section', '?')}]"
        context_parts.append(f"**Chunk {i}** {source}")
        context_parts.append(chunk["text"][:1500])  # Cap individual chunk size
        context_parts.append("")

    context_parts.append("--- END RAG CONTEXT ---\n")
    return "\n".join(context_parts)


def get_collection_stats(ticker: str) -> dict:
    """Get stats about the stored documents for a ticker."""
    collection = get_collection(ticker)
    count = collection.count()

    if count == 0:
        return {"total_chunks": 0, "doc_types": [], "sections": []}

    # Sample metadata to get doc types and sections
    sample = collection.peek(limit=min(count, 100))
    doc_types = set()
    sections = set()
    filenames = set()

    for meta in (sample.get("metadatas") or []):
        doc_types.add(meta.get("doc_type", "unknown"))
        sections.add(meta.get("section", "unknown"))
        filenames.add(meta.get("filename", "unknown"))

    return {
        "total_chunks": count,
        "doc_types": list(doc_types),
        "sections": list(sections),
        "filenames": list(filenames),
    }


def clear_collection(ticker: str) -> bool:
    """Delete the collection for a specific ticker."""
    try:
        client = get_chroma_client()
        base_name = ticker.replace('-', '_').replace('&', '_')
        upper_name = f"novus_{base_name.upper()}"
        lower_name = f"novus_{base_name.lower()}"
        
        existing = [c.name for c in client.list_collections()]
        collection_name = lower_name if lower_name in existing else upper_name

        collection_name = re.sub(r'[^a-zA-Z0-9_]', '_', collection_name)[:63]
        client.delete_collection(collection_name)
        return True
    except ValueError:
        return False


def evaluate_draft_with_critic(draft: str, context: str) -> dict:
    """
    Acts as the Entailment Judge.
    Routes through the centralized DeepSeek V3 LLMClient instead of deprecated genai.
    NEVER auto-approves on failure — all errors result in rejection.
    Returns: {"passes": bool, "feedback": str}
    """
    from core.llm_client import get_v3_client

    system_prompt = """You are an aggressive Forensic Accounting Editor.
Review the DRAFT against the raw RAG CONTEXT.

RULES:
1. If the DRAFT contains ANY numerical figure, date, or financial claim NOT physically present in the CONTEXT, you must fail it.
2. If the DRAFT hallucinates an audit opinion or legal status, fail it.
3. Respond ONLY with valid JSON: {"passes": true/false, "feedback": "..."}
4. If it passes, feedback should be empty string. If it fails, provide a 1-sentence precise error."""

    user_content = f"""--- RAG CONTEXT ---
{context}

--- DRAFT TO EVALUATE ---
{draft}"""

    try:
        llm = get_v3_client()
        response = llm.call_simple(system_prompt, user_content)

        # Extract JSON from response (handle markdown fences)
        json_str = response.strip()
        if json_str.startswith("```"):
            json_str = json_str.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(json_str)
        # Validate schema
        if "passes" not in result:
            return {"passes": False, "feedback": "Critic returned malformed schema. Draft rejected for safety."}
        return result

    except json.JSONDecodeError:
        return {"passes": False, "feedback": "Critic failed to output valid JSON. Rewrite the draft to be safer."}
    except Exception as e:
        error_str = str(e)
        logger.error(f"[Critic] Error: {error_str}")
        # NEVER auto-approve. All failures are hard rejections.
        return {"passes": False, "feedback": f"Critic Engine Error: {error_str}. Draft rejected — maintain strict grounding."}


def get_dynamic_context(ticker: str, optimized_query: str, doc_types: list[str] = None) -> str:
    """Fetches context based on an LLM-optimized user query."""
    
    # query() now supports lists natively via your $in update
    results = query(ticker, optimized_query, top_k=8, doc_type_filter=doc_types)

    if not results:
        return "NO FINANCIAL DATA FOUND IN REPOSITORY."

    context_parts = ["--- RAG CONTEXT (from uploaded company documents) ---\n"]
    for i, chunk in enumerate(results, 1):
        meta = chunk.get("metadata", {})
        context_parts.append(f"**Source: {meta.get('filename')} | Type: {meta.get('doc_type')}**")
        context_parts.append(chunk["text"])
        context_parts.append("\n")
        
    return "\n".join(context_parts)
