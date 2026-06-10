"""
structured_data_fetcher.py — Structured API Data Router for Novus MAS

FIX 2: Bypasses unstructured PDF OCR for quant agents by routing clean,
structured financial data from Screener.in directly to quant agents
(forensic_quant, capital_allocator) while NLP agents continue receiving raw text.

Architecture:
  PDF Text → narrative_decoder, moat_architect
  Screener JSON API → forensic_quant, capital_allocator, (valuation)
"""

import json
import logging
from typing import Dict, Any, Optional

from screener_scraper import fetch_screener_tables

logger = logging.getLogger(__name__)


# ── Agent Classification ─────────────────────────────────────────────────────

QUANT_AGENTS = {"fsa_quant", "forensic_quant", "capital_allocator"}
NLP_AGENTS = {"narrative_decoder", "moat_architect", "forensic_investigator"}



# ── Boundary Normalization ───────────────────────────────────────────────
# Parse, Don't Validate: all Screener.in quirks are stripped HERE.
# Internal code only ever sees: profit_loss, balance_sheet, cash_flow, etc.
# with year-keyed dicts like {"Mar 2024": {"Revenue": 15747.0, ...}}.

import re

# Screener UI key → internal contract key
_KEY_MAP = {
    "Profit & Loss": "profit_loss",
    "Balance Sheet": "balance_sheet",
    "Cash Flows": "cash_flow",
    "Quarterly Results": "quarterly_results",
    "Ratios": "ratios",
    "Shareholding Pattern": "shareholding",
    "Peer Comparison": "peers",
}

# Strict fiscal year column filter — rejects TTM, junk, merged cells
_FISCAL_YEAR_RE = re.compile(r"^(Mar|Jun|Sep|Dec)\s\d{4}$")


def _to_float(value) -> Optional[float]:
    """Best-effort conversion of scraped cell values into floats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in ("nan", "-", ""):
        return None
    cleaned = text.replace(",", "").replace("%", "").replace("x", "").replace("₹", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _transpose_table(rows: list) -> dict:
    """Convert Screener's list-of-dicts into year-keyed dict.

    Input:  [{"Line Item": "Revenue", "Mar 2023": "14,000", "TTM": "15,200"}, ...]
    Output: {"Mar 2023": {"Revenue": 14000.0}, ...}

    TTM and non-fiscal-year columns are REJECTED by _FISCAL_YEAR_RE.
    """
    if not isinstance(rows, list) or not rows:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue

        # Identify the label column (usually 'Line Item' or 'Unnamed: 0')
        label = ""
        label_key = ""
        for k, v in row.items():
            k_str = str(k)
            if k_str in ("Line Item", "Unnamed: 0") or k_str.startswith("Unnamed"):
                label = str(v).strip()
                label_key = k_str
                break
        if not label and row:
            first_key = next(iter(row))
            label = str(row[first_key]).strip()
            label_key = str(first_key)

        if not label:
            continue

        # ── CRITICAL: Sanitize label at the boundary ──
        # Screener uses non-breaking spaces (\xa0) and trailing '+' in its HTML.
        # e.g., "Sales\xa0+" → "Sales", "Net Profit\xa0+" → "Net Profit"
        # If we don't strip these here, _fget fails, tools return empty {},
        # and the LLM hallucinates to fill the gap.
        label = label.replace('\xa0', ' ').strip()
        if label.endswith('+'):
            label = label[:-1].strip()

        # Allocate values to their respective fiscal years
        for col_name, value in row.items():
            col_str = str(col_name).strip()
            if col_str == label_key:
                continue
            # Only accept actual fiscal year columns (reject TTM, junk, etc.)
            if not _FISCAL_YEAR_RE.match(col_str):
                continue
            if col_str not in out:
                out[col_str] = {}
            out[col_str][label] = _to_float(value)

    return out


def _clean_peer_rows(rows: list) -> list:
    """Normalize the peer-comparison table: company-per-row with metric
    columns (CMP, P/E, Mkt Cap, ROCE, ...). Numerics coerced, labels kept.
    The 'Median: N Co.' row Screener appends IS the sector median."""
    if not isinstance(rows, list):
        return []
    cleaned = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out: Dict[str, Any] = {}
        for k, v in row.items():
            key = str(k).replace('\xa0', ' ').strip()
            if key.lower() in ("s.no.", "s.no"):
                continue
            text = str(v).replace('\xa0', ' ').strip() if v is not None else ""
            num = _to_float(text)
            # Name/label columns stay strings; metric columns become floats.
            out[key] = num if (num is not None and key.lower() != "name") else text
        if out.get("Name"):
            cleaned.append(out)
    return cleaned


def _normalize_tables(raw_tables: dict) -> dict:
    """Normalize Screener's raw output into our internal data contract.

    1. Renames keys: "Profit & Loss" → "profit_loss"
    2. Transposes rows: list-of-dicts → year-keyed dicts
    3. Filters columns: TTM and non-fiscal-year columns are rejected
    4. Peer comparison is row-per-company, not year-keyed — passed through
       with numeric coercion instead of transposition
    """
    normalized = {}
    for screener_key, rows in raw_tables.items():
        internal_key = _KEY_MAP.get(screener_key, screener_key.lower().replace(" ", "_").replace("&", "and"))
        if internal_key == "peers":
            normalized[internal_key] = _clean_peer_rows(rows)
        else:
            normalized[internal_key] = _transpose_table(rows)
    return normalized


class StructuredDataFetcher:
    """
    Institutional data feed abstraction layer.
    
    Currently backed by Screener.in (live scraping).
    Designed for future drop-in replacement with XBRL feeds,
    FactSet API, Capitaline, or Bloomberg B-PIPE.
    
    All data normalization (key remapping, structure transposition,
    TTM filtering) happens HERE at the boundary. Downstream agents
    and tools only ever see pristine, year-keyed dicts.
    """

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}

    def fetch(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch structured financial data for a given ticker.
        Returns normalized tables with canonical keys + auto-detected sector.
        Results are cached per-session to avoid redundant HTTP requests.

        Resilience: every successful fetch is persisted as a last-known-good
        snapshot. If the live fetch fails or returns nothing (Screener outage,
        datacenter IP block), the snapshot is served instead — flagged with
        'from_snapshot' / 'snapshot_at' so downstream can log staleness.
        """
        ticker = ticker.upper().strip()

        if ticker in self._cache:
            logger.info(f"[StructuredDataFetcher] Cache hit for {ticker}")
            return self._cache[ticker]

        logger.info(f"[StructuredDataFetcher] Fetching structured data for {ticker}")

        error: str = ""
        try:
            raw = fetch_screener_tables(ticker)
            raw_tables = raw.get("tables", {})

            if raw_tables:
                # ── Normalize at the boundary ──
                tables = _normalize_tables(raw_tables)

                structured = {
                    "ticker": ticker,
                    "source": raw.get("source", "screener.in"),
                    "sector": raw.get("sector", "General"),
                    "tables": tables,
                    # Keep the original Screener shape (UI titles + row dicts) so
                    # the display endpoint / charts can be served from a snapshot
                    # too — those consume raw_tables, not the normalized contract.
                    "raw_tables": raw_tables,
                }

                self._cache[ticker] = structured
                self._persist_snapshot(ticker, structured)
                logger.info(
                    f"[StructuredDataFetcher] ✅ {ticker} ({structured['sector']}): "
                    f"{len(tables)} tables, "
                    f"sections={list(tables.keys())}"
                )
                return structured

            error = "No structured data available"
            logger.warning(f"[StructuredDataFetcher] No tables found for {ticker}")

        except Exception as e:
            error = str(e)
            logger.error(f"[StructuredDataFetcher] Failed for {ticker}: {e}")

        # ── Live fetch failed → last-known-good snapshot ──
        snapshot = self._load_snapshot(ticker)
        if snapshot and snapshot.get("tables"):
            snapshot["from_snapshot"] = True
            snapshot["error"] = f"Live fetch failed ({error}); serving snapshot from {snapshot.get('snapshot_at')}"
            logger.warning(
                f"[StructuredDataFetcher] ⚠️ {ticker}: live fetch failed — serving "
                f"last-known-good snapshot from {snapshot.get('snapshot_at')}"
            )
            self._cache[ticker] = snapshot
            return snapshot

        return {"ticker": ticker, "sector": "General", "tables": {}, "error": error}

    @staticmethod
    def _persist_snapshot(ticker: str, structured: Dict[str, Any]) -> None:
        try:
            from core.memory import get_memory
            get_memory().store_screener_snapshot(ticker, structured)
        except Exception as e:
            logger.warning(f"[StructuredDataFetcher] Snapshot persist failed for {ticker}: {e}")

    @staticmethod
    def _load_snapshot(ticker: str) -> Optional[Dict[str, Any]]:
        try:
            from core.memory import get_memory
            return get_memory().get_screener_snapshot(ticker)
        except Exception as e:
            logger.warning(f"[StructuredDataFetcher] Snapshot load failed for {ticker}: {e}")
            return None


    @staticmethod
    def _coerce_numeric(value: Any) -> Optional[float]:
        """Best-effort conversion of scraped cell values into floats."""
        if value is None:
            return None

        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip()
        if not text or text.lower() == "nan" or text == "-":
            return None

        cleaned = (
            text.replace(",", "")
            .replace("%", "")
            .replace("x", "")
            .replace("₹", "")
            .strip()
        )

        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _find_row_label(row: Dict[str, Any]) -> str:
        for key, value in row.items():
            if key.startswith("Unnamed") or key.lower() == "line item":
                return str(value).strip()

        first_value = next(iter(row.values()), "")
        return str(first_value).strip()

    @classmethod
    def _extract_latest_metric(
        cls, rows: list[Dict[str, Any]], aliases: tuple[str, ...]
    ) -> Optional[float]:
        """
        Find the latest numeric value for a row whose label loosely matches one
        of the provided aliases.
        """
        normalized_aliases = tuple(alias.lower() for alias in aliases)

        for row in rows:
            label = cls._find_row_label(row).lower()
            if not label:
                continue

            if not any(alias in label for alias in normalized_aliases):
                continue

            numeric_values: list[float] = []
            for key, value in row.items():
                key_lower = str(key).lower()
                if key_lower.startswith("unnamed") or key_lower == "line item":
                    continue

                numeric = cls._coerce_numeric(value)
                if numeric is not None:
                    numeric_values.append(numeric)

            if numeric_values:
                return numeric_values[-1]

        return None

    def fetch_raw(self, ticker: str) -> Dict[str, Any]:
        """
        Backward-compatible accessor used by planning-time assumption tuning.
        Returns the fetched tables plus a few flattened top-level metrics.
        """
        structured = self.fetch(ticker)
        ratios_rows = structured.get("tables", {}).get("Ratios", [])

        debt_equity = self._extract_latest_metric(
            ratios_rows,
            (
                "debt to equity",
                "debt/equity",
                "debt-equity",
                "debt equity",
            ),
        )

        return {
            **structured,
            "debt_equity": debt_equity,
        }

    def format_as_context(self, ticker: str) -> str:
        """
        Convert structured financial tables into canonical JSON so downstream
        agents can parse it deterministically instead of reverse-engineering a
        markdown table dump.
        """
        data = self.fetch(ticker)
        tables = data.get("tables", {})

        if not tables:
            return f"[NO STRUCTURED DATA AVAILABLE FOR {ticker}]"

        payload = {
            "ticker": data.get("ticker", ticker.upper()),
            "source": data.get("source", "screener.in"),
            "tables": tables,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def should_receive_structured_data(agent_name: str) -> bool:
        """Determine if an agent should receive structured API data."""
        return agent_name in QUANT_AGENTS

    @staticmethod
    def should_receive_text_only(agent_name: str) -> bool:
        """Determine if an agent should receive only raw text context."""
        return agent_name in NLP_AGENTS


# ── Module-level singleton ───────────────────────────────────────────────────

_fetcher_instance: Optional[StructuredDataFetcher] = None


def get_structured_data_fetcher() -> StructuredDataFetcher:
    """Get or create the singleton StructuredDataFetcher instance."""
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = StructuredDataFetcher()
    return _fetcher_instance


if __name__ == "__main__":
    fetcher = StructuredDataFetcher()
    ctx = fetcher.format_as_context("HINDUNILVR")
    print(ctx[:2000])
    print(f"\n--- Total context length: {len(ctx)} chars ---")
