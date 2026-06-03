"""
novus_v3/core/tools.py — Tool Registry + Shared Financial Analysis Tools

Every v3 agent gets access to these document/data tools.
Agents can ALSO register their own specialized tools.

Design principle: The LLM decides WHAT to investigate.
Python computes the MATH. The LLM NARRATES the findings.
"""

import json
import re
import numpy as np
from typing import Callable, Optional
from dataclasses import dataclass
from rag_engine import query as rag_query


# ═══════════════════════════════════════════════════════════════════════════
# Tool Registry
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict        # JSON Schema
    handler: Callable       # Python function to execute


class ToolRegistry:
    """Registry of callable tools available to an agent."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> "ToolRegistry":
        self._tools[tool.name] = tool
        return self                         # allow chaining

    def to_api_format(self) -> list[dict]:
        """Format for DeepSeek / OpenAI function-calling API."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict) -> str:
        tool = self._tools.get(name)
        if not tool:
            return json.dumps({"error": f"Tool '{name}' not found"})
        try:
            result = tool.handler(**arguments)
            if isinstance(result, (dict, list)):
                return json.dumps(result, ensure_ascii=False, default=str)
            return str(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


# ═══════════════════════════════════════════════════════════════════════════
# Shared Tools — available to EVERY agent
# ═══════════════════════════════════════════════════════════════════════════

def build_shared_tools(document_text: str, financial_tables: dict, ticker: str = "") -> ToolRegistry:
    """
    Core toolkit every v3 agent gets.  Individual agents extend this
    with their own specialized tools via build_agent_tools().
    """
    reg = ToolRegistry()

    # ── 1. Document keyword search ────────────────────────────────────
    reg.register(Tool(
        name="search_document",
        description=(
            "Search the annual report / earnings transcript for a topic. "
            "Returns up to 3 relevant passages, each with a `chunk_id` that uniquely "
            "identifies the source. When you quote a passage in a finding, you MUST "
            "include the returned `chunk_id` in your citations[] so the platform can "
            "render Hover-to-Verify provenance for compliance reviewers. "
            "Use for: 'related party transactions', 'auditor qualification', "
            "'goodwill impairment', 'contingent liabilities', 'segment revenue', "
            "'management guidance', 'capex plans', 'debt maturity profile', etc."
        ),
        parameters=_schema({
            "query":       ("string",  True,  "What to search for"),
            "max_results": ("integer", False, "1-5, default 3"),
            "min_year":    ("integer", False, "Only return documents from this year or newer (e.g. 2023). Crucial for current strategy."),
        }),
        handler=lambda query, max_results=3, min_year=None: _search_doc(document_text, query, max_results, ticker, min_year),
    ))

    # ── 2. Get a specific page / note ─────────────────────────────────
    reg.register(Tool(
        name="get_page_content",
        description=(
            "Retrieve text around a page or note reference. "
            "Indian annual reports bury critical disclosures in notes 30-50. "
            "Use when you see 'Refer Note 42' or 'as per Schedule III'."
        ),
        parameters=_schema({
            "reference": ("string", True, "e.g. 'note 42', 'page 188', 'schedule III'"),
        }),
        handler=lambda reference: _get_page(document_text, reference),
    ))

    # ── 3. Financial line-item lookup ─────────────────────────────────
    reg.register(Tool(
        name="get_metric",
        description=(
            "Get a financial line item across all available years.\n"
            "Tables: profit_loss, balance_sheet, cash_flow.\n"
            "Supports fuzzy matching — e.g. 'Revenue' matches 'Revenue from Operations'.\n"
            "WARNING: If making claims about 'current strategy', limit your analysis to the last 12-24 months. Label older data as 'Historical Context'."
        ),
        parameters=_schema({
            "line_item": ("string", True,  "e.g. 'Revenue from Operations', 'Trade Receivables'"),
            "table":     ("string", True,  "profit_loss | balance_sheet | cash_flow"),
        }),
        handler=lambda line_item, table: _get_metric(financial_tables, line_item, table),
    ))

    # ── 4. Python-computed ratio (Law 2 enforcer) ─────────────────────
    reg.register(Tool(
        name="compute_ratio",
        description=(
            "Compute a financial ratio using PYTHON. You MUST use this tool "
            "for ALL numerical calculations. Never compute ratios yourself. "
            "Example: compute_ratio('Other Income', 'Profit before tax', 'profit_loss', 'Mar 2024')"
        ),
        parameters=_schema({
            "numerator":   ("string", True,  "Numerator line item"),
            "denominator": ("string", True,  "Denominator line item"),
            "table":       ("string", True,  "profit_loss | balance_sheet | cash_flow"),
            "year":        ("string", True,  "e.g. 'Mar 2024'"),
        }),
        handler=lambda numerator, denominator, table, year: _compute_ratio(
            financial_tables, numerator, denominator, table, year
        ),
    ))

    # ── 5. Year-over-year comparison ──────────────────────────────────
    reg.register(Tool(
        name="compare_years",
        description=(
            "Compare a metric between two fiscal years. "
            "Returns values, absolute change, and % change — computed in Python."
        ),
        parameters=_schema({
            "metric": ("string", True,  "Line item name"),
            "year1":  ("string", True,  "Earlier year, e.g. 'Mar 2023'"),
            "year2":  ("string", True,  "Later year, e.g. 'Mar 2024'"),
            "table":  ("string", True,  "profit_loss | balance_sheet | cash_flow"),
        }),
        handler=lambda metric, year1, year2, table: _compare_years(
            financial_tables, metric, year1, year2, table
        ),
    ))

    # ── 6. Statistical anomaly detection ──────────────────────────────
    reg.register(Tool(
        name="detect_anomaly",
        description=(
            "Scan a line item across all years for anomalies: "
            "sudden spikes (>30% YoY), drops, or divergence from another item. "
            "Use to detect channel stuffing (receivables vs revenue divergence), "
            "aggressive capitalisation (CWIP vs depreciation), etc."
        ),
        parameters=_schema({
            "line_item":    ("string", True,  "Primary metric to scan"),
            "table":        ("string", True,  "profit_loss | balance_sheet | cash_flow"),
            "compare_with": ("string", False, "Optional second metric for divergence check"),
        }),
        handler=lambda line_item, table, compare_with=None: _detect_anomaly(
            financial_tables, line_item, table, compare_with
        ),
    ))

    # ── 7. Multi-year CAGR ────────────────────────────────────────────
    reg.register(Tool(
        name="compute_cagr",
        description=(
            "Compute the Compound Annual Growth Rate of a line item "
            "between two years. Returns percentage. Uses Python math."
        ),
        parameters=_schema({
            "line_item": ("string", True, "Line item name"),
            "table":     ("string", True, "profit_loss | balance_sheet | cash_flow"),
            "from_year": ("string", True, "Start year"),
            "to_year":   ("string", True, "End year"),
        }),
        handler=lambda line_item, table, from_year, to_year: _compute_cagr(
            financial_tables, line_item, table, from_year, to_year
        ),
    ))

    # ── 8. List available years and line items ────────────────────────
    reg.register(Tool(
        name="list_available_data",
        description=(
            "List all available years and line-item names for a given table. "
            "Call this FIRST if you're unsure what data is available. "
            "WARNING: If making claims about 'current strategy', limit your analysis to the last 12-24 months. Label older data as 'Historical Context'."
        ),
        parameters=_schema({
            "table": ("string", True, "profit_loss | balance_sheet | cash_flow"),
        }),
        handler=lambda table: _list_available(financial_tables, table),
    ))

    # ── 9. Quarterly anomaly verification ─────────────────────────────
    reg.register(Tool(
        name="verify_quarterly_spike",
        description=(
            "Cross-reference a flagged quarterly anomaly against actual quarterly data. "
            "Returns QoQ and YoY verification with adjacent quarters and cross-metric checks. "
            "Use when you see a suspicious spike or drop in any metric."
        ),
        parameters=_schema({
            "metric":            ("string", True,  "Line item to verify, e.g. 'Other Income', 'Net Profit'"),
            "quarter":           ("string", True,  "Quarter to check, e.g. 'Dec 2024'"),
            "expected_variance": ("number", False, "Threshold as decimal, e.g. 0.30 for 30%. Default 0.30"),
        }),
        handler=lambda metric, quarter, expected_variance=0.30: _verify_quarterly(
            financial_tables, ticker, metric, quarter, expected_variance
        ),
    ))

    # ── 10. Quarterly trends ──────────────────────────────────────────
    reg.register(Tool(
        name="get_quarterly_trends",
        description=(
            "Get QoQ sequential growth rates for a metric across all available quarters. "
            "Returns chronological list with QoQ and YoY change percentages."
        ),
        parameters=_schema({
            "metric": ("string", True, "Line item name, e.g. 'Sales', 'Net Profit', 'Operating Profit'"),
        }),
        handler=lambda metric: _get_quarterly_trends(financial_tables, metric),
    ))

    # ── 11. Segment breakdown ─────────────────────────────────────────
    reg.register(Tool(
        name="get_segment_breakdown",
        description=(
            "Extract business segment revenue, margins, and growth from annual reports "
            "and earnings call transcripts using RAG + LLM extraction. "
            "Works for any company whose documents are ingested. "
            "Returns segment names, revenue, growth rates, and margins."
        ),
        parameters=_schema({
            "period": ("string", False, "'latest' or specific period like 'Mar 2025'. Default 'latest'"),
        }),
        handler=lambda period="latest": _get_segments(ticker, period, financial_tables),
    ))

    # ── 12. Volume vs price growth ────────────────────────────────────
    reg.register(Tool(
        name="get_volume_vs_price_growth",
        description=(
            "Extract volume growth vs price/mix growth decomposition from earnings call "
            "transcripts. This data is ONLY available in management commentary. "
            "Also returns rural/urban mix if disclosed."
        ),
        parameters=_schema({
            "period": ("string", False, "'latest' or specific period. Default 'latest'"),
        }),
        handler=lambda period="latest": _get_volume_price(ticker, period, financial_tables),
    ))

    return reg


# ═══════════════════════════════════════════════════════════════════════════
# Memory-layer tools — expose the MemoryLayer PM product methods as agent tools
# ═══════════════════════════════════════════════════════════════════════════

def build_memory_tools(ticker: str) -> "ToolRegistry":
    """Wrap the 4 PM-facing memory methods as agent-callable tools.

    Used primarily by the Copilot chat agent. Empty returns (e.g. when the
    memory DB has no data for this ticker yet) are valid signals the agent
    should handle by saying "no inconsistencies detected yet" rather than
    retrying.
    """
    # Lazy import so this module can be imported independently of the memory layer
    from core.memory import get_memory

    reg = ToolRegistry()
    ticker = (ticker or "").upper().strip()

    reg.register(Tool(
        name="get_management_inconsistencies",
        description=(
            "Return every detected narrative inconsistency for this ticker — "
            "cross-quarter cases where management's story on the same metric "
            "reversed (e.g. Q2 'temporary supply chain' -> Q3 'structural "
            "demand softness'). Each row includes both facts side-by-side with "
            "their fiscal_period, verified source citations, severity, and a "
            "rationale. Use THIS TOOL when the user asks about 'red flags', "
            "'narrative shifts', 'management credibility', 'what's changed', "
            "'story drift', or 'bear case'. Empty list means no contradictions "
            "have been persisted yet for this ticker."
        ),
        parameters=_schema({}),
        handler=lambda: get_memory().get_management_inconsistencies(ticker),
    ))

    reg.register(Tool(
        name="get_thesis_drift",
        description=(
            "Return the quarter-by-quarter evolution of verified claims for a "
            "single metric_category on this ticker. Use to answer 'how has "
            "management framed X across quarters?' Example categories: "
            "demand_commentary, margins, guidance, capex, promoter_pledge, "
            "regional_revenue, distribution_reach. Returns a time-ordered list "
            "of {fiscal_period, verified_fact, agent_name, citations}."
        ),
        parameters=_schema({
            "metric_category": ("string", True,
                "One of: margins, revenue_growth, volume_growth, roic, roce, "
                "cash_flow, working_capital, leverage, capex, guidance, "
                "demand_commentary, pricing_power, governance, promoter_pledge, "
                "rpt, auditor, capital_allocation, mna, dividend_policy, "
                "distribution_reach, regional_revenue, segments, product_mix, "
                "competitive_position, compliance, litigation."),
            "max_periods": ("integer", False,
                "Max number of fiscal periods to return. Default 8."),
        }),
        handler=lambda metric_category, max_periods=8: (
            get_memory().get_thesis_drift(ticker, metric_category, int(max_periods))
        ),
    ))

    reg.register(Tool(
        name="get_negative_space_report",
        description=(
            "Return the 'Silent Signals' report: metrics this company has "
            "refused to disclose across >= min_periods consecutive fiscal "
            "periods. Use to answer 'what is the company hiding?' or "
            "'suggest questions for the next earnings call'. Returns rows "
            "with metric_category, periods_count, periods (comma-joined), "
            "total_occurrences, and a sample_description."
        ),
        parameters=_schema({
            "min_periods": ("integer", False,
                "Minimum consecutive fiscal periods the gap must appear in. "
                "Default 2."),
        }),
        handler=lambda min_periods=2: (
            get_memory().get_negative_space_report(ticker, int(min_periods))
        ),
    ))

    reg.register(Tool(
        name="get_audit_trail",
        description=(
            "Fetch the full audit trail (original_claim, verified_fact, "
            "citations, fiscal_period, agent_name) for a single mistake row "
            "by its integer id. Use when a prior tool returned a mistake_id "
            "and the user wants to see the underlying source text."
        ),
        parameters=_schema({
            "mistake_id": ("integer", True,
                "The integer id returned by get_management_inconsistencies "
                "or any other memory-layer tool."),
        }),
        handler=lambda mistake_id: (
            get_memory().get_audit_trail(int(mistake_id))
        ),
    ))

    return reg


# ═══════════════════════════════════════════════════════════════════════════
# Tool Implementations (Pure Python — no LLM involvement)
# ═══════════════════════════════════════════════════════════════════════════

def _schema(fields: dict) -> dict:
    """Shorthand for building JSON Schema parameters."""
    properties = {}
    required = []
    for name, (typ, req, desc) in fields.items():
        properties[name] = {"type": typ, "description": desc}
        if req:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def _fuzzy_get(data: dict, key: str):
    """Try exact match, then NBSP-normalized, then case-insensitive substring match."""
    if key in data:
        return data[key]
    # Normalize: strip NBSP and trailing '+'
    def _norm(s):
        return s.replace('\xa0', ' ').rstrip('+').strip().lower()
    key_norm = _norm(key)
    for k, v in data.items():
        k_norm = _norm(k)
        if key_norm == k_norm:
            return v
    for k, v in data.items():
        k_norm = _norm(k)
        if key_norm in k_norm or k_norm in key_norm:
            return v
    return None


def _search_doc(text: str, query: str, max_results: int = 3, ticker: str = "", min_year: int = None) -> list[dict]:
    """BM25-style keyword search over document paragraphs, with an option to use semantic RAG.

    Returns list of {passage, score, position, chunk_id, doc_id, page} so the caller can
    cite results back in structured citations[] for Hover-to-Verify provenance.
    """
    if ticker:
        results = rag_query(ticker, query, top_k=max_results, min_year=min_year)
        if results:
            return [
                {
                    "passage": r["text"][:1000],
                    "score": r["relevance"],
                    "position": "rag_chunk",
                    "chunk_id": r.get("chunk_id"),
                    "doc_id": (r.get("metadata") or {}).get("filename"),
                    "page": (r.get("metadata") or {}).get("page"),
                    "section": (r.get("metadata") or {}).get("section"),
                }
                for r in results
            ]

    # Fallback to dumb BM25 search — no chunk_id available for raw paragraphs
    terms = [t.lower() for t in query.split() if len(t) > 2]
    if not terms:
        return [{"passage": "Empty query", "score": 0}]

    paragraphs = re.split(r'\n\s*\n', text)
    scored = []
    for i, para in enumerate(paragraphs):
        para = para.strip()
        if len(para) < 30:
            continue
        lower = para.lower()
        score = sum(lower.count(t) for t in terms)
        # Boost for exact phrase match
        if query.lower() in lower:
            score += 10
        if score > 0:
            scored.append((score, i, para))

    scored.sort(key=lambda x: -x[0])
    return [
        {
            "passage": p[:1000],
            "score": s,
            "position": f"para_{idx}",
            "chunk_id": None,
            "doc_id": None,
        }
        for s, idx, p in scored[:max_results]
    ] or [{"passage": "No relevant content found.", "score": 0, "chunk_id": None, "doc_id": None}]


def _get_page(text: str, reference: str) -> dict:
    ref = reference.lower().strip()
    # Try multiple patterns
    for pattern in [ref, ref.replace("note ", "note no. "), ref.replace("note ", "notes ")]:
        idx = text.lower().find(pattern)
        if idx != -1:
            start = max(0, idx - 200)
            end = min(len(text), idx + 3000)
            return {"content": text[start:end], "found": True, "position": idx}
    return {"content": f"'{reference}' not found in document.", "found": False}


def _get_metric(tables: dict, line_item: str, table: str) -> dict:
    tbl = tables.get(table, {})
    results = {}
    for year, items in tbl.items():
        if isinstance(items, dict):
            val = _fuzzy_get(items, line_item)
            if val is not None:
                results[year] = val
    return {"line_item": line_item, "table": table, "values": results}


def _compute_ratio(tables: dict, num: str, den: str, table: str, year: str) -> dict:
    tbl = tables.get(table, {})
    year_data = tbl.get(year, {})
    n = _fuzzy_get(year_data, num)
    d = _fuzzy_get(year_data, den)
    
    if d in (None, "", 0, 0.0):
        return {"value": "Data Unavailable", "status": "DATA_NOT_AVAILABLE"}
        
    try:
        ratio = round(n / d, 4)
        if ratio > 3650 or ratio < -3650:
            return {"value": "Data Unavailable: Out of Bounds", "status": "DATA_NOT_AVAILABLE"}
    except (ZeroDivisionError, TypeError):
        return {"value": "Data Unavailable", "status": "DATA_NOT_AVAILABLE"}
        
    return {
        "value": ratio, "status": "OK",
        "numerator": num, "n_value": n,
        "denominator": den, "d_value": d,
        "ratio": ratio, "pct": f"{ratio*100:.2f}%", "year": year,
    }


def _compare_years(tables: dict, metric: str, y1: str, y2: str, table: str) -> dict:
    tbl = tables.get(table, {})
    v1 = _fuzzy_get(tbl.get(y1, {}), metric)
    v2 = _fuzzy_get(tbl.get(y2, {}), metric)
    result = {"metric": metric, "year1": y1, "val1": v1, "year2": y2, "val2": v2}
    
    if v1 in (None, "", 0, 0.0):
        result["abs_change"] = "Data Unavailable"
        result["pct_change"] = "Data Unavailable"
        return result
        
    try:
        if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
            abs_change = round(v2 - v1, 2)
            pct_change = round(((v2 - v1) / abs(v1)) * 100, 2)
            if pct_change > 365000 or pct_change < -365000:
                result["abs_change"] = "Data Unavailable: Out of Bounds"
                result["pct_change"] = "Data Unavailable: Out of Bounds"
            else:
                result["abs_change"] = abs_change
                result["pct_change"] = pct_change
    except (ZeroDivisionError, TypeError):
        result["abs_change"] = "Data Unavailable"
        result["pct_change"] = "Data Unavailable"
        
    return result


def _detect_anomaly(tables: dict, item: str, table: str, compare: str = None) -> dict:
    data = _get_metric(tables, item, table)
    vals = data.get("values", {})
    years = sorted(vals.keys())
    anomalies = []
    for i in range(1, len(years)):
        p, c = vals.get(years[i-1]), vals.get(years[i])
        if p in (None, "", 0, 0.0):
            continue
        try:
            if isinstance(p, (int, float)) and isinstance(c, (int, float)):
                chg = ((c - p) / abs(p)) * 100
                if abs(chg) > 365000:
                    continue
                if abs(chg) > 30:
                    anomalies.append({
                        "year": years[i], "prev": p, "curr": c,
                        "change_pct": round(chg, 1),
                        "type": "SPIKE" if chg > 30 else "DROP",
                    })
        except (ZeroDivisionError, TypeError):
            pass

    result = {"line_item": item, "values": vals, "anomalies": anomalies}

    if compare:
        comp = _get_metric(tables, compare, table).get("values", {})
        ratios = []
        for y in years:
            v1, v2 = vals.get(y), comp.get(y)
            if v2 in (None, "", 0, 0.0):
                continue
            try:
                if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                    ratio = v1 / v2
                    if ratio > 3650 or ratio < -3650:
                        continue
                    ratios.append({"year": y, "ratio": round(ratio, 4)})
            except (ZeroDivisionError, TypeError):
                pass
        if len(ratios) >= 2:
            first, last = ratios[0]["ratio"], ratios[-1]["ratio"]
            if first not in (None, "", 0, 0.0):
                try:
                    drift = ((last - first) / abs(first)) * 100
                    if drift < 365000 and drift > -365000:
                        result["divergence"] = {
                            "vs": compare, "ratios": ratios,
                            "drift_pct": round(drift, 1), "is_diverging": abs(drift) > 20,
                        }
                except (ZeroDivisionError, TypeError):
                    pass
    return result


def _compute_cagr(tables: dict, item: str, table: str, y1: str, y2: str) -> dict:
    data = _get_metric(tables, item, table).get("values", {})
    v1, v2 = data.get(y1), data.get(y2)
    if v1 in (None, "", 0, 0.0):
        return {"error": "Data Unavailable"}
    if not (isinstance(v1, (int, float)) and isinstance(v2, (int, float))):
        return {"error": f"Missing data: {y1}={v1}, {y2}={v2}"}
    if v1 <= 0 or v2 <= 0:
        return {"error": "Cannot compute CAGR with non-positive values"}
    # Estimate years between
    try:
        yr1 = int(re.search(r'(\d{4})', y1).group(1))
        yr2 = int(re.search(r'(\d{4})', y2).group(1))
        n = yr2 - yr1
    except (AttributeError, ValueError, TypeError):
        n = 1
    if n <= 0:
        return {"error": f"Invalid year range: {y1} to {y2}"}
    try:
        cagr = ((v2 / v1) ** (1 / n) - 1) * 100
        if cagr > 365000 or cagr < -365000:
            return {"error": "Data Unavailable: Out of Bounds"}
    except (ZeroDivisionError, TypeError):
        return {"error": "Data Unavailable"}
        
    return {"cagr_pct": round(cagr, 2), "from": v1, "to": v2, "years": n}


def _list_available(tables: dict, table: str) -> dict:
    tbl = tables.get(table, {})
    years = sorted(tbl.keys())
    items = set()
    for yr_data in tbl.values():
        if isinstance(yr_data, dict):
            items.update(yr_data.keys())
    return {"table": table, "years": years, "line_items": sorted(items)}


# ── New Tool Handlers (Tasks 2 & 4) ──────────────────────────────────────────

def _verify_quarterly(tables: dict, ticker: str, metric: str, quarter: str, expected_variance: float = 0.30) -> dict:
    """Bridge to quarterly_analyzer.verify_quarterly_anomaly."""
    try:
        from agents.quarterly_analyzer import verify_quarterly_anomaly, load_quarterly
        qdata = load_quarterly(ticker, tables)
        if qdata is None:
            return {"error": "No quarterly data available", "metric": metric, "quarter": quarter}
        result = verify_quarterly_anomaly(metric, quarter, expected_variance, qdata)
        return result.to_dict()
    except Exception as e:
        return {"error": f"Quarterly verification failed: {e}", "metric": metric}


def _get_quarterly_trends(tables: dict, metric: str) -> dict:
    """Bridge to quarterly_analyzer.get_qoq_trends for a single metric."""
    try:
        from agents.quarterly_analyzer import load_quarterly, get_qoq_trends
        # Use empty ticker — we just need the tables data
        qdata = load_quarterly("", tables)
        if qdata is None:
            return {"error": "No quarterly data available", "metric": metric}
        trends = get_qoq_trends(qdata, [metric])
        if metric in trends:
            return {"metric": metric, "quarters": [q.to_dict() for q in trends[metric]]}
        return {"error": f"Metric '{metric}' not found in quarterly data", "metric": metric}
    except Exception as e:
        return {"error": f"Quarterly trends failed: {e}", "metric": metric}


def _get_segments(ticker: str, period: str, tables: dict) -> dict:
    """Bridge to segment_extractor.get_segments."""
    try:
        from agents.segment_extractor import get_segments
        sector = tables.get("_sector", "General") if isinstance(tables, dict) else "General"
        return get_segments(ticker, period, sector)
    except Exception as e:
        return {"error": f"Segment extraction failed: {e}", "ticker": ticker}


def _get_volume_price(ticker: str, period: str, tables: dict) -> dict:
    """Bridge to segment_extractor.get_volume_price_split."""
    try:
        from agents.segment_extractor import get_volume_price_split
        sector = tables.get("_sector", "General") if isinstance(tables, dict) else "General"
        return get_volume_price_split(ticker, period, sector)
    except Exception as e:
        return {"error": f"Volume/price extraction failed: {e}", "ticker": ticker}

