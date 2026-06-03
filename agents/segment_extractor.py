"""
agents/segment_extractor.py — Segment Breakdown Extractor

3-stage pipeline:
  Stage 1: Vertical Discovery — determine company type from sector string
  Stage 2: RAG retrieval with Table-Header Anchors (Ind AS 108, segment notes)
  Stage 3: DeepSeek Schema-First extraction (1 API call per analysis)

Also extracts volume vs. price growth and rural/urban mix from transcript
text using targeted regex patterns.

Designed for scalability to thousands of tickers — no manual per-company work.
"""

import re
import json
import logging
from typing import Optional, Literal
from dataclasses import dataclass, field, asdict

from rag_engine import query as rag_query

logger = logging.getLogger(__name__)


# ── Data Contracts ───────────────────────────────────────────────────────────

@dataclass
class SegmentBreakdown:
    segment_name: str
    revenue_current: Optional[float] = None
    revenue_prior: Optional[float] = None
    revenue_growth_pct: Optional[float] = None
    operating_margin_pct: Optional[float] = None
    volume_growth_pct: Optional[float] = None         # From transcript only
    value_growth_pct: Optional[float] = None           # Price × Mix, from transcript
    commentary: Optional[str] = None                   # Key mgmt quote
    data_source: str = "unknown"

    def to_dict(self):
        return asdict(self)


@dataclass
class SegmentAnalysis:
    ticker: str = ""
    period: str = ""
    company_type: str = "other"
    segment_reporting_type: str = "business"
    segments: list = field(default_factory=list)
    unallocated_income: Optional[float] = None
    volume_vs_value_summary: Optional[str] = None
    rural_urban_mix: Optional[dict] = None
    data_gaps: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["segments"] = [s.to_dict() if hasattr(s, 'to_dict') else s for s in self.segments]
        return d


# ── Stage 1: Vertical Discovery ──────────────────────────────────────────────

VERTICAL_MAP = {
    "fast moving consumer goods": "manufacturer",
    "fmcg": "manufacturer",
    "consumer goods": "manufacturer",
    "personal care": "manufacturer",
    "food products": "manufacturer",
    "household products": "manufacturer",
    "banks": "bank",
    "banking": "bank",
    "private sector bank": "bank",
    "public sector bank": "bank",
    "nbfc": "bank",
    "financial services": "bank",
    "insurance": "bank",
    "it - software": "it_services",
    "information technology": "it_services",
    "technology": "it_services",
    "consulting": "it_services",
    "pharmaceuticals": "pharma",
    "healthcare": "pharma",
    "drug": "pharma",
    "auto": "auto",
    "automobile": "auto",
    "cement": "manufacturer",
    "metals": "manufacturer",
    "chemicals": "manufacturer",
    "oil & gas": "manufacturer",
    "power": "utility",
    "telecom": "telecom",
    "real estate": "real_estate",
}


def _detect_vertical(sector: str) -> str:
    """Map sector string to vertical classification."""
    sector_lower = sector.lower().strip()
    for key, vertical in VERTICAL_MAP.items():
        if key in sector_lower:
            return vertical
    return "other"


# ── Stage 2: RAG Retrieval with Table-Header Anchors ─────────────────────────

# Vertical-specific RAG search queries
VERTICAL_QUERIES = {
    "manufacturer": [
        "Ind AS 108 segment revenue operating segments segmental information",
        "business segment results revenue profit",
        "segment wise revenue breakdown",
    ],
    "bank": [
        "segment reporting retail wholesale treasury",
        "Ind AS 108 operating segments",
        "segment wise interest income advances",
    ],
    "it_services": [
        "segment revenue vertical industry geography",
        "geographical revenue breakdown Americas Europe",
        "industry vertical wise revenue contribution",
    ],
    "pharma": [
        "segment revenue formulations API bulk drug",
        "geographical revenue India US Europe domestic export",
        "therapeutic segment wise revenue",
    ],
    "auto": [
        "segment revenue automotive farm equipment",
        "vehicle segment volume revenue",
        "domestic export segment breakdown",
    ],
    "other": [
        "segment revenue operating segments",
        "Ind AS 108 segmental information",
        "business segment wise results",
    ],
}

# Transcript-specific queries for volume / rural data
TRANSCRIPT_QUERIES = [
    "volume growth underlying volume",
    "price growth value growth price-mix",
    "rural contribution urban mix rural recovery",
]


# ── Stage 3: Extraction Patterns ─────────────────────────────────────────────

VOLUME_PATTERNS = [
    re.compile(r"(?:underlying\s+)?volume\s+growth\s+(?:of|was|at|stood at)\s+(\d+(?:\.\d+)?)\s*%", re.I),
    re.compile(r"volumes?\s+(?:grew|grew by|declined by|increased by|fell by)\s+(\d+(?:\.\d+)?)\s*%", re.I),
    re.compile(r"volume\s+(?:growth|decline)\s+of\s+(?:about\s+)?(\d+(?:\.\d+)?)\s*%", re.I),
]

PRICE_PATTERNS = [
    re.compile(r"price(?:\s*[-/]\s*mix)?\s+growth\s+(?:of|was|at)\s+(\d+(?:\.\d+)?)\s*%", re.I),
    re.compile(r"pricing\s+(?:contributed|impact|growth)\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*%", re.I),
    re.compile(r"(?:price|realization)\s+(?:increase|growth|hike)\s+of\s+(\d+(?:\.\d+)?)\s*%", re.I),
]

RURAL_PATTERNS = [
    re.compile(r"rural\s+(?:contribution|share|mix|revenue)\s+(?:of|was|at|stood at)\s+(\d+(?:\.\d+)?)\s*%", re.I),
    re.compile(r"rural\s*[:/]\s*urban\s+(?:split|mix|ratio)\s+(?:of|was|at)\s+(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)", re.I),
    re.compile(r"rural\s+(?:markets?|demand|growth)\s+(?:contributed|accounted for)\s+(\d+(?:\.\d+)?)\s*%", re.I),
]


SEGMENT_EXTRACTION_PROMPT = """You are a financial data extractor for institutional research.

Company: {ticker} ({sector})
Company Type: {company_type}

Extract segment information from the following document excerpts.

CRITICAL RULES:
1. Separate OPERATING segment revenue from non-operating other income.
2. Only extract segments EXPLICITLY named in the source — do NOT infer or guess.
3. If volume/price growth percentages are not explicitly stated, set to null.
4. revenue_current and revenue_prior should be in the same unit (Cr, Mn, etc.) as the source.
5. For segment_reporting_type: "business" for product/division segments, "geographical" for India/International.

Return valid JSON ONLY matching this EXACT structure:
{{
  "segment_reporting_type": "business|geographical|product|vertical",
  "segments": [
    {{
      "segment_name": "Home Care",
      "revenue_current": 21560,
      "revenue_prior": 20100,
      "revenue_growth_pct": 7.3,
      "operating_margin_pct": 18.5,
      "commentary": "Driven by premiumization in Surf Excel"
    }}
  ],
  "unallocated_income": 450
}}

SOURCE DOCUMENTS:
{rag_chunks}"""


# ── Cache ────────────────────────────────────────────────────────────────────

_segment_cache: dict[tuple[str, str], SegmentAnalysis] = {}


# ── Public API ───────────────────────────────────────────────────────────────

def get_segments(
    ticker: str,
    period: str = "latest",
    sector: str = "General",
    document_text: str = "",
) -> dict:
    """
    Extract business segment breakdown for a company.
    Uses RAG + LLM extraction — fully automated for any company.
    """
    cache_key = (ticker.upper(), period)
    if cache_key in _segment_cache:
        logger.info(f"[SegmentExtractor] Cache hit for {cache_key}")
        return _segment_cache[cache_key].to_dict()

    analysis = SegmentAnalysis(ticker=ticker.upper(), period=period)

    # Stage 1: Vertical Discovery
    company_type = _detect_vertical(sector)
    analysis.company_type = company_type
    logger.info(f"[SegmentExtractor] {ticker} → {company_type} (sector: {sector})")

    # Stage 2: RAG retrieval
    queries = VERTICAL_QUERIES.get(company_type, VERTICAL_QUERIES["other"])
    all_chunks = []

    for q in queries:
        results = rag_query(ticker, q, top_k=3)
        if results:
            all_chunks.extend(r["text"][:1500] for r in results)

    # Also search transcript for segment context
    for q in TRANSCRIPT_QUERIES:
        results = rag_query(ticker, q, top_k=2)
        if results:
            all_chunks.extend(r["text"][:1000] for r in results)

    if not all_chunks:
        analysis.data_gaps.append("No segment data found in RAG — annual reports may not be ingested")
        _segment_cache[cache_key] = analysis
        return analysis.to_dict()

    # Deduplicate chunks
    seen = set()
    unique_chunks = []
    for chunk in all_chunks:
        chunk_key = chunk[:100]
        if chunk_key not in seen:
            seen.add(chunk_key)
            unique_chunks.append(chunk)

    combined_text = "\n\n---\n\n".join(unique_chunks[:10])  # Cap at 10 chunks

    # Stage 3: LLM extraction
    try:
        from llm_clients import call_gemini

        prompt = SEGMENT_EXTRACTION_PROMPT.format(
            ticker=ticker,
            sector=sector,
            company_type=company_type,
            rag_chunks=combined_text[:10000],
        )

        response = call_gemini(
            "You are a financial data extractor. Output valid JSON only.",
            prompt,
        )

        if response and not response.startswith("Error"):
            clean = response.strip()
            if '```json' in clean:
                clean = clean.split('```json', 1)[1].rsplit('```', 1)[0]
            elif '```' in clean:
                clean = clean.split('```', 1)[1].rsplit('```', 1)[0]

            parsed = json.loads(clean.strip())

            analysis.segment_reporting_type = parsed.get("segment_reporting_type", "business")
            analysis.unallocated_income = parsed.get("unallocated_income")

            for seg in parsed.get("segments", []):
                analysis.segments.append(SegmentBreakdown(
                    segment_name=seg.get("segment_name", "Unknown"),
                    revenue_current=seg.get("revenue_current"),
                    revenue_prior=seg.get("revenue_prior"),
                    revenue_growth_pct=seg.get("revenue_growth_pct"),
                    operating_margin_pct=seg.get("operating_margin_pct"),
                    commentary=seg.get("commentary"),
                    data_source="annual_report",
                ))

            logger.info(f"[SegmentExtractor] Extracted {len(analysis.segments)} segments for {ticker}")

    except json.JSONDecodeError as e:
        analysis.data_gaps.append(f"LLM returned invalid JSON: {e}")
    except Exception as e:
        analysis.data_gaps.append(f"LLM extraction failed: {e}")

    # Extract volume/price/rural data from transcript text
    _extract_volume_price_rural(analysis, combined_text)

    if not analysis.segments:
        analysis.data_gaps.append("No segments could be extracted — manual review recommended")

    _segment_cache[cache_key] = analysis
    return analysis.to_dict()


def get_volume_price_split(
    ticker: str,
    period: str = "latest",
    sector: str = "General",
) -> dict:
    """
    Extract volume growth vs price/mix growth from earnings call transcripts.
    This data is ONLY available in management commentary — never in structured filings.
    """
    # Search transcripts for volume/price data
    results = rag_query(
        ticker,
        "volume growth underlying volume price growth value growth price-mix realization",
        top_k=5,
    )

    if not results:
        return {
            "ticker": ticker,
            "period": period,
            "volume_growth_pct": None,
            "value_growth_pct": None,
            "data_source": "not_found",
            "data_gaps": ["No volume/price growth data found in transcripts"],
        }

    combined = " ".join(r["text"] for r in results)

    volume_pct = _extract_first_match(combined, VOLUME_PATTERNS)
    price_pct = _extract_first_match(combined, PRICE_PATTERNS)
    rural_data = _extract_rural_urban(combined)

    return {
        "ticker": ticker,
        "period": period,
        "volume_growth_pct": volume_pct,
        "value_growth_pct": price_pct,
        "rural_urban_mix": rural_data,
        "data_source": "concall_transcript",
        "raw_context": combined[:1000],
    }


# ── Internal Helpers ─────────────────────────────────────────────────────────

def _extract_first_match(text: str, patterns: list) -> Optional[float]:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _extract_rural_urban(text: str) -> Optional[dict]:
    for pattern in RURAL_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            if len(groups) >= 2:
                return {
                    "rural_pct": float(groups[0]),
                    "urban_pct": float(groups[1]),
                    "source": "concall_transcript",
                }
            elif len(groups) == 1:
                rural = float(groups[0])
                return {
                    "rural_pct": rural,
                    "urban_pct": round(100 - rural, 1),
                    "source": "concall_transcript",
                }
    return None


def _extract_volume_price_rural(analysis: SegmentAnalysis, text: str):
    """Extract volume, price, and rural/urban data from combined RAG text."""
    volume_pct = _extract_first_match(text, VOLUME_PATTERNS)
    price_pct = _extract_first_match(text, PRICE_PATTERNS)
    rural_data = _extract_rural_urban(text)

    if volume_pct is not None or price_pct is not None:
        parts = []
        if volume_pct is not None:
            parts.append(f"Volume growth: {volume_pct}%")
        if price_pct is not None:
            parts.append(f"Price/Mix growth: {price_pct}%")
        analysis.volume_vs_value_summary = " | ".join(parts)
    else:
        analysis.data_gaps.append("Volume vs. price growth not found in available transcripts")

    if rural_data:
        analysis.rural_urban_mix = rural_data
    else:
        analysis.data_gaps.append("Rural/Urban mix not found in available transcripts")
