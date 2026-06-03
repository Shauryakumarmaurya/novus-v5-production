# vision_parser.py — Vision-to-Markdown Parser for Financial Documents
"""
Novus Vision Parser — VLM-based PDF page extraction for financial tables.

Standard PDF text extractors (PyMuPDF get_text()) destroy table grid structure,
leaving numbers orphaned from their column headers. This module uses Google's
Gemini Flash VLM to "read" page screenshots and produce structured Markdown
with pipe-format tables preserved.

Usage:
  As a library:
    from vision_parser import VisionParser
    parser = VisionParser()
    md = parser.parse_page(fitz_page, page_num=14)

  As CLI:
    python vision_parser.py --pdf path/to/report.pdf --output output.md

Stack:
  Slicer:  PyMuPDF (fitz) — renders pages as high-res PNGs in memory
  Parser:  Google Gemini 2.0 Flash — vision-language model
  Output:  Markdown with pipe tables and [PAGE_N] provenance headers
"""

import os
import io
import time
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

GEMINI_MODEL = os.getenv("VISION_PARSE_MODEL", "gemini-2.0-flash")
SLEEP_BETWEEN_PAGES = float(os.getenv("VISION_PARSE_SLEEP_S", "1.0"))
ZOOM_FACTOR = float(os.getenv("VISION_PARSE_ZOOM", "2.0"))
MAX_RETRIES = int(os.getenv("VISION_PARSE_MAX_RETRIES", "5"))
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".vision_cache")

# ── Extraction Prompt ────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are an institutional-grade financial data extractor. Convert this page image to Markdown with ABSOLUTE FIDELITY.

RULES:
1. Preserve ALL tables using pipe format: | Column A | Column B |
2. Include table separator rows: | --- | --- |
3. Preserve ALL numbers EXACTLY as printed (including ₹, commas, parentheses for negatives)
4. Preserve ALL footnote references (e.g., "Note 3", asterisks)
5. Do NOT summarize, interpret, or rephrase ANY content
6. Do NOT add commentary, greetings, or markdown code fences
7. For non-table text, preserve headings using # syntax and paragraphs as-is
8. If a cell is empty, use a single dash: -
9. For multi-line cells, join content with a space on one line

Output ONLY the raw markdown. No preamble. No closing remarks."""


# ── Table Page Detection (Two-Tier Heuristic) ───────────────────────────────

# Tier 1: Core financial statement keywords → triggers at orphan_ratio > 0.15
_TIER1_KEYWORDS = [
    'balance sheet', 'profit and loss', 'statement of profit',
    'cash flow', 'total assets', 'revenue from operations',
    'shareholders', 'equity and liabilities', 'particulars',
    'notes to', 'schedule',
]

# Tier 2: Governance & disclosure tables (text-heavy, fewer orphan numbers)
#         → triggers at orphan_ratio > 0.08
_TIER2_KEYWORDS = [
    'related party', 'contingent liabilities', 'remuneration',
    'audit fees', 'managerial remuneration', 'key management personnel',
    'loans and advances', 'segment reporting', 'deferred tax',
    'employee benefit', 'gratuity', 'lease liabilities',
]


def is_table_page(page: fitz.Page) -> bool:
    """Heuristic: determine if a page has financial table structure worth vision-parsing.

    Uses a two-tier keyword system:
      Tier 0 (No keywords): triggers only at orphan_ratio > 0.25
      Tier 1 (Financial Statements): Balance Sheet, P&L, Cash Flow, etc.
             → triggers at orphan_ratio > 0.15
      Tier 2 (Governance/Disclosure): Related Party, Remuneration, Audit Fees, etc.
             → triggers at orphan_ratio > 0.08 (lower threshold because
               these pages are text-heavy but still have critical tabular data)
    """
    text = page.get_text()
    lines = text.strip().split('\n')
    if len(lines) < 5:
        return False

    # Count orphan number lines (digits only, after stripping commas/parens/dashes)
    orphan_count = sum(
        1 for l in lines
        if l.strip().replace(',', '').replace('.', '').replace('(', '').replace(')', '').replace('-', '').isdigit()
        and len(l.strip()) > 0
    )

    ratio = orphan_count / len(lines)
    lower = text.lower()

    has_tier1 = any(kw in lower for kw in _TIER1_KEYWORDS)
    has_tier2 = any(kw in lower for kw in _TIER2_KEYWORDS)

    # Tier 0: Pure number-density (no keyword boost needed)
    if ratio > 0.25:
        return True

    # Tier 1: Financial statements — moderate threshold
    if has_tier1 and ratio > 0.15:
        return True

    # Tier 2: Governance/disclosure — low threshold (text-heavy tables)
    if has_tier2 and ratio > 0.08:
        return True

    return False


# ── Core Vision Parser ───────────────────────────────────────────────────────

class VisionParser:
    """VLM-based page parser using Google Gemini Flash."""

    def __init__(self, api_key: Optional[str] = None, model: str = GEMINI_MODEL):
        self.model_name = model
        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "")

        if not self._api_key or self._api_key == "replace_me":
            raise ValueError(
                "GEMINI_API_KEY is not configured. "
                "Set it in .env or pass api_key= to VisionParser()."
            )

        # Initialize the new google-genai SDK
        from google import genai
        self._client = genai.Client(api_key=self._api_key)
        logger.info(f"[VisionParser] Initialized with model={self.model_name}")

    def _render_page_image(self, page: fitz.Page) -> Image.Image:
        """Render a fitz.Page as a high-resolution PIL Image (in memory, no disk I/O)."""
        mat = fitz.Matrix(ZOOM_FACTOR, ZOOM_FACTOR)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        return Image.open(io.BytesIO(img_bytes))

    def parse_page(self, page: fitz.Page, page_num: int) -> str:
        """Parse a single PDF page using the VLM.

        Args:
            page: A fitz.Page object.
            page_num: 0-indexed page number (used for error messages).

        Returns:
            Markdown string of the page content. On failure, returns an error placeholder.
        """
        img = self._render_page_image(page)

        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=[EXTRACTION_PROMPT, img],
                )
                md_text = response.text.strip()

                # Strip markdown code fences if the model wraps output (common LLM habit)
                if md_text.startswith("```markdown"):
                    md_text = md_text[len("```markdown"):].strip()
                if md_text.startswith("```"):
                    md_text = md_text[3:].strip()
                if md_text.endswith("```"):
                    md_text = md_text[:-3].strip()

                return md_text

            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                    # Try to extract Gemini's suggested retry delay
                    import re
                    retry_match = re.search(r'retry\s*(?:in|Delay[\'"]?\s*:\s*[\'"]?)(\d+)', err_str)
                    suggested_wait = int(retry_match.group(1)) if retry_match else None
                    
                    # Use suggested wait or exponential backoff (min 15s for free tier)
                    wait = max(suggested_wait or (15 * (2 ** attempt)), 15)
                    logger.warning(
                        f"[VisionParser] Rate limited on page {page_num + 1}. "
                        f"Waiting {wait}s (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"[VisionParser] Failed to parse page {page_num + 1} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES}): {e}"
                    )
                    if attempt == MAX_RETRIES - 1:
                        return f"[ERROR: Failed to extract page {page_num + 1} — {type(e).__name__}: {e}]"
                    time.sleep(SLEEP_BETWEEN_PAGES)

        return f"[ERROR: Exhausted {MAX_RETRIES} retries for page {page_num + 1}]"

    def parse_pdf(
        self,
        pdf_path: str,
        output_md_path: Optional[str] = None,
        *,
        hybrid: bool = True,
        use_cache: bool = True,
        progress_callback=None,
    ) -> str:
        """Parse an entire PDF using the hybrid approach.

        Args:
            pdf_path: Path to the PDF file.
            output_md_path: Optional path to write the output markdown.
            hybrid: If True, only vision-parse table pages; use get_text() for others.
                    If False, vision-parse every page.
            use_cache: If True, check/write a cached .md file to skip re-parsing.
            progress_callback: Optional callable(msg: str) for progress updates.

        Returns:
            The full markdown string.
        """
        pdf_path = os.path.abspath(pdf_path)

        # ── Cache check ──
        if use_cache:
            cache_path = self._cache_path(pdf_path)
            if cache_path.exists():
                logger.info(f"[VisionParser] Cache hit: {cache_path}")
                cached_md = cache_path.read_text(encoding="utf-8")
                if output_md_path:
                    Path(output_md_path).write_text(cached_md, encoding="utf-8")
                return cached_md

        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        text_parts = []
        vision_count = 0
        text_count = 0

        for i, page in enumerate(doc):
            page_header = f"\n\n### [PAGE_{i + 1}]\n\n"

            should_vision = (not hybrid) or is_table_page(page)

            if should_vision:
                if progress_callback:
                    progress_callback(
                        f"Vision parsing page {i + 1}/{total_pages} "
                        f"(table page detected)"
                    )
                md = self.parse_page(page, page_num=i)
                text_parts.append(page_header + md)
                vision_count += 1
                time.sleep(SLEEP_BETWEEN_PAGES)
            else:
                if progress_callback and i % 20 == 0:
                    progress_callback(
                        f"Text extracting pages {i + 1}-{min(i + 20, total_pages)}/{total_pages}"
                    )
                raw_text = page.get_text()
                text_parts.append(page_header + raw_text)
                text_count += 1

        doc.close()

        full_md = "".join(text_parts)

        logger.info(
            f"[VisionParser] Completed {os.path.basename(pdf_path)}: "
            f"{vision_count} vision-parsed, {text_count} text-extracted, "
            f"{total_pages} total pages"
        )

        # ── Write output ──
        if output_md_path:
            Path(output_md_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_md_path).write_text(full_md, encoding="utf-8")
            logger.info(f"[VisionParser] Wrote {output_md_path}")

        # ── Cache write ──
        if use_cache:
            cache_path = self._cache_path(pdf_path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(full_md, encoding="utf-8")
            # Write a companion metadata file
            meta = {
                "source_pdf": pdf_path,
                "total_pages": total_pages,
                "vision_parsed_pages": vision_count,
                "text_extracted_pages": text_count,
                "model": self.model_name,
                "zoom_factor": ZOOM_FACTOR,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            meta_path = cache_path.with_suffix(".meta.json")
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            logger.info(f"[VisionParser] Cached to {cache_path}")

        return full_md

    @staticmethod
    def _cache_path(pdf_path: str) -> Path:
        """Generate a deterministic cache path for a PDF based on its content hash."""
        with open(pdf_path, "rb") as f:
            # Read first 64KB + last 64KB + file size for fast hashing
            head = f.read(65536)
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read(65536)

        content_id = hashlib.sha256(head + tail + str(size).encode()).hexdigest()[:16]
        basename = Path(pdf_path).stem
        return Path(CACHE_DIR) / f"{basename}_{content_id}.md"


# ── Parse from bytes (for rag_engine integration) ───────────────────────────

def parse_pdf_bytes(
    file_bytes: bytes,
    filename: str,
    parser: Optional[VisionParser] = None,
    hybrid: bool = True,
    progress_callback=None,
) -> str:
    """Parse a PDF from raw bytes using the hybrid vision approach.

    This is the primary entry point for rag_engine.py integration.

    Args:
        file_bytes: Raw PDF file bytes.
        filename: Original filename (used for logging and cache key).
        parser: Optional pre-initialized VisionParser instance.
                If None, creates one using env vars.
        hybrid: If True, only vision-parse table pages.
        progress_callback: Optional callable(msg: str) for progress updates.

    Returns:
        Full markdown string with [PAGE_N] headers on every page.
    """
    if parser is None:
        try:
            parser = VisionParser()
        except ValueError as e:
            logger.warning(f"[VisionParser] Cannot initialize: {e}. Falling back to text-only.")
            return _fallback_text_extract(file_bytes)

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    total_pages = len(doc)
    text_parts = []
    vision_count = 0

    # ── Cache check (hash the bytes directly) ──
    content_hash = hashlib.sha256(file_bytes[:65536] + str(len(file_bytes)).encode()).hexdigest()[:16]
    cache_path = Path(CACHE_DIR) / f"{Path(filename).stem}_{content_hash}.md"

    if cache_path.exists():
        logger.info(f"[VisionParser] Cache hit for {filename}")
        doc.close()
        return cache_path.read_text(encoding="utf-8")

    for i, page in enumerate(doc):
        page_header = f"\n\n### [PAGE_{i + 1}]\n\n"

        should_vision = (not hybrid) or is_table_page(page)

        if should_vision:
            if progress_callback:
                progress_callback(f"Vision parsing page {i + 1}/{total_pages} of {filename}")
            md = parser.parse_page(page, page_num=i)
            text_parts.append(page_header + md)
            vision_count += 1
            time.sleep(SLEEP_BETWEEN_PAGES)
        else:
            raw_text = page.get_text()
            text_parts.append(page_header + raw_text)

    doc.close()

    full_md = "".join(text_parts)

    logger.info(
        f"[VisionParser] {filename}: {vision_count} vision-parsed / {total_pages} total pages"
    )

    # Cache result
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(full_md, encoding="utf-8")
    except Exception as e:
        logger.warning(f"[VisionParser] Cache write failed: {e}")

    return full_md


def _fallback_text_extract(file_bytes: bytes) -> str:
    """Fallback: standard text extraction with PAGE headers (no VLM)."""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    parts = []
    for i, page in enumerate(doc):
        parts.append(f"\n\n### [PAGE_{i + 1}]\n\n{page.get_text()}")
    doc.close()
    return "".join(parts)


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    ap = argparse.ArgumentParser(
        description="Vision-to-Markdown parser for financial PDFs"
    )
    ap.add_argument("--pdf", required=True, help="Path to the input PDF")
    ap.add_argument("--output", "-o", help="Path to write the output .md file")
    ap.add_argument(
        "--full", action="store_true",
        help="Vision-parse ALL pages (not just table pages)"
    )
    ap.add_argument(
        "--no-cache", action="store_true",
        help="Skip cache read/write"
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Only detect table pages; don't actually call the VLM"
    )
    args = ap.parse_args()

    if args.dry_run:
        doc = fitz.open(args.pdf)
        table_pages = []
        for i, page in enumerate(doc):
            if is_table_page(page):
                text = page.get_text()
                lines = text.strip().split('\n')
                orphan = sum(
                    1 for l in lines
                    if l.strip().replace(',', '').replace('.', '').replace('(', '').replace(')', '').replace('-', '').isdigit()
                    and len(l.strip()) > 0
                )
                ratio = orphan / max(len(lines), 1)
                title = text[:60].strip().replace('\n', ' ')
                table_pages.append((i + 1, ratio, title))

        print(f"\nTotal pages: {len(doc)}")
        print(f"Table pages detected: {len(table_pages)}")
        print(f"Text-only pages: {len(doc) - len(table_pages)}")
        print(f"\n{'Page':>6}  {'Ratio':>7}  Title")
        print("-" * 70)
        for pg, r, title in table_pages:
            print(f"{pg:6d}  {r:6.1%}  {title[:50]}")
        doc.close()
        return

    def progress(msg):
        print(f"  → {msg}")

    parser = VisionParser()
    output = args.output or args.pdf.replace(".pdf", "_parsed.md")

    md = parser.parse_pdf(
        args.pdf,
        output_md_path=output,
        hybrid=not args.full,
        use_cache=not args.no_cache,
        progress_callback=progress,
    )

    print(f"\n✓ Done. Output: {output} ({len(md):,} chars)")


if __name__ == "__main__":
    main()
