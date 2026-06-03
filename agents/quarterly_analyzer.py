"""
agents/quarterly_analyzer.py — Quarterly Data Ingestion & Anomaly Verification

Pure Python + Pandas module with Pydantic contracts.  No LLM calls.
Reads quarterly data from:
  1. financial_tables["quarterly_results"] (live Screener scrape via structured_data_fetcher)
  2. Local CSV fallback at company_docs/{dir}/03_Financial_Data/quarterly_results.csv

Provides:
  - load_quarterly()             → pd.DataFrame
  - get_qoq_trends()            → sequential growth rates for key metrics
  - verify_quarterly_anomaly()  → cross-quarter verification of flagged anomalies
"""

import os
import csv
import io
import logging
from typing import Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


# ── Data Contracts ───────────────────────────────────────────────────────────

@dataclass
class QuarterlyMetric:
    quarter: str                          # "Dec 2024"
    value: Optional[float] = None
    qoq_change_pct: Optional[float] = None   # vs previous quarter
    yoy_change_pct: Optional[float] = None   # vs same quarter last year

    def to_dict(self):
        return asdict(self)


@dataclass
class AnomalyVerification:
    metric: str                           # "Other Income"
    quarter: str                          # "Dec 2024"
    confirmed: bool = False               # True if variance exceeds threshold
    actual_variance: Optional[float] = None  # actual QoQ change, e.g. 264%
    expected_variance: float = 0.30       # threshold caller expects
    context: str = ""                     # human-readable explanation
    adjacent_quarters: list = field(default_factory=list)
    cross_metric_check: Optional[dict] = None

    def to_dict(self):
        d = asdict(self)
        d["adjacent_quarters"] = [q if isinstance(q, dict) else q.to_dict() for q in self.adjacent_quarters]
        return d


# ── Loader ───────────────────────────────────────────────────────────────────

def _to_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    text = str(val).strip()
    if not text or text.lower() in ("nan", "-", ""):
        return None
    cleaned = text.replace(",", "").replace("%", "").replace("₹", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def load_quarterly(
    ticker: str,
    financial_tables: dict,
    company_docs_root: str = None,
) -> Optional[dict]:
    """
    Load quarterly data as a dict: {metric_name: {quarter: value}}.
    Primary source: financial_tables["quarterly_results"] (already normalized by structured_data_fetcher).
    Fallback: CSV from disk.
    """
    # Primary: from structured_data_fetcher output
    qr = financial_tables.get("quarterly_results", {})
    if qr:
        logger.info(f"[QuarterlyAnalyzer] Loaded {len(qr)} quarters from structured tables")
        return _transpose_to_metric_keyed(qr)

    # Fallback: CSV from disk
    if company_docs_root:
        csv_path = _find_quarterly_csv(ticker, company_docs_root)
        if csv_path:
            return _load_csv_as_metric_keyed(csv_path)

    logger.warning(f"[QuarterlyAnalyzer] No quarterly data found for {ticker}")
    return None


def _transpose_to_metric_keyed(qr: dict) -> dict:
    """Convert year-keyed → metric-keyed: {metric: {quarter: value}}"""
    result = {}
    for quarter, items in qr.items():
        if not isinstance(items, dict):
            continue
        for metric, value in items.items():
            if metric not in result:
                result[metric] = {}
            result[metric][quarter] = value
    return result


def _find_quarterly_csv(ticker: str, root: str) -> Optional[str]:
    """Search company_docs for a quarterly_results.csv belonging to this ticker."""
    ticker_upper = ticker.upper()
    for dirpath, dirnames, filenames in os.walk(root):
        if ticker_upper in dirpath.upper():
            for f in filenames:
                if f.lower() == "quarterly_results.csv":
                    return os.path.join(dirpath, f)
    return None


def _load_csv_as_metric_keyed(csv_path: str) -> Optional[dict]:
    """Load a Screener-format CSV into metric-keyed dict."""
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)
        if not rows:
            return None

        headers = [h.strip() for h in rows[0]]
        quarters = headers[1:]  # first col is line item name

        result = {}
        for row in rows[1:]:
            if not row or not row[0].strip():
                continue
            metric = row[0].replace('\xa0', ' ').rstrip('+').strip()
            if not metric or metric.lower() == "raw pdf":
                continue
            result[metric] = {}
            for i, quarter in enumerate(quarters):
                if i + 1 < len(row):
                    result[metric][quarter] = _to_float(row[i + 1])
        return result
    except Exception as e:
        logger.error(f"[QuarterlyAnalyzer] CSV load failed: {e}")
        return None


from datetime import datetime

def _sort_quarters(quarters: list[str]) -> list[str]:
    """Sort quarters chronologically (e.g. 'Mar 2023', 'Dec 2024')."""
    parsed = []
    for q in quarters:
        try:
            # Assuming format like "Dec 2024" or "Sep 2023"
            dt = datetime.strptime(q.strip(), "%b %Y")
            parsed.append((dt, q))
        except ValueError:
            # Fallback to string sort if unknown format
            parsed.append((datetime.min, q))
    return [q for dt, q in sorted(parsed, key=lambda x: (x[0], x[1]))]


# ── QoQ Trends ───────────────────────────────────────────────────────────────

def get_qoq_trends(
    quarterly_data: dict,
    metrics: list[str] = None,
) -> dict[str, list[QuarterlyMetric]]:
    """
    Get sequential QoQ and YoY growth rates for specified metrics.
    Returns: {metric_name: [QuarterlyMetric, ...]}
    """
    if quarterly_data is None:
        return {}

    if metrics is None:
        metrics = ["Net Profit", "Other Income", "Operating Profit", "Sales"]

    results = {}
    for metric in metrics:
        metric_data = _fuzzy_find_metric(quarterly_data, metric)
        if not metric_data:
            continue

        quarters = _sort_quarters(list(metric_data.keys()))
        entries = []

        for i, q in enumerate(quarters):
            val = metric_data.get(q)
            qoq = None
            yoy = None

            # QoQ: compare to previous quarter
            if i > 0:
                prev_val = metric_data.get(quarters[i - 1])
                if val is not None and prev_val is not None and prev_val != 0:
                    qoq = round(((val - prev_val) / abs(prev_val)) * 100, 2)

            # YoY: compare to same quarter last year (4 quarters back)
            if i >= 4:
                yoy_val = metric_data.get(quarters[i - 4])
                if val is not None and yoy_val is not None and yoy_val != 0:
                    yoy = round(((val - yoy_val) / abs(yoy_val)) * 100, 2)

            entries.append(QuarterlyMetric(
                quarter=q,
                value=val,
                qoq_change_pct=qoq,
                yoy_change_pct=yoy,
            ))

        results[metric] = entries
    return results


def _fuzzy_find_metric(data: dict, target: str) -> Optional[dict]:
    """Fuzzy lookup of metric name in quarterly data dict."""
    target_lower = target.lower()
    # Exact match
    if target in data:
        return data[target]
    # Normalized match
    for key, val in data.items():
        if key.lower().replace('\xa0', ' ').rstrip('+').strip() == target_lower:
            return val
    # Substring match
    for key, val in data.items():
        key_clean = key.lower().replace('\xa0', ' ').rstrip('+').strip()
        if target_lower in key_clean or key_clean in target_lower:
            return val
    return None


# ── Anomaly Verification ─────────────────────────────────────────────────────

def verify_quarterly_anomaly(
    metric: str,
    quarter: str,
    expected_variance: float,
    quarterly_data: dict,
) -> AnomalyVerification:
    """
    Cross-reference a flagged anomaly against quarterly data.
    
    Returns a structured verification with:
    - confirmed: True if actual variance exceeds expected_variance
    - actual_variance: the real QoQ change percentage
    - adjacent_quarters: context from surrounding quarters
    - cross_metric_check: related metrics (e.g., if Other Income spiked, did PBT too?)
    """
    result = AnomalyVerification(
        metric=metric,
        quarter=quarter,
        expected_variance=expected_variance,
    )

    metric_data = _fuzzy_find_metric(quarterly_data, metric)
    if not metric_data:
        result.context = f"Metric '{metric}' not found in quarterly data"
        return result

    quarters = _sort_quarters(list(metric_data.keys()))
    if quarter not in quarters:
        # Try fuzzy quarter match
        for q in quarters:
            if quarter.lower() in q.lower():
                quarter = q
                break
        else:
            result.context = f"Quarter '{quarter}' not found. Available: {quarters}"
            return result

    q_idx = quarters.index(quarter)
    curr_val = metric_data.get(quarter)

    # Get adjacent quarters (2 before, 1 after)
    adj_range = range(max(0, q_idx - 2), min(len(quarters), q_idx + 2))
    for i in adj_range:
        q = quarters[i]
        val = metric_data.get(q)
        qoq = None
        if i > 0:
            prev = metric_data.get(quarters[i - 1])
            if val is not None and prev is not None and prev != 0:
                qoq = round(((val - prev) / abs(prev)) * 100, 2)
        result.adjacent_quarters.append(QuarterlyMetric(
            quarter=q, value=val, qoq_change_pct=qoq,
        ))

    # Compute actual QoQ variance
    if q_idx > 0:
        prev_val = metric_data.get(quarters[q_idx - 1])
        if curr_val is not None and prev_val is not None and prev_val != 0:
            actual_pct = ((curr_val - prev_val) / abs(prev_val))
            result.actual_variance = round(actual_pct * 100, 2)
            result.confirmed = abs(actual_pct) > expected_variance
            result.context = (
                f"{metric} in {quarter}: {curr_val:,.0f} vs prev {prev_val:,.0f} → "
                f"{result.actual_variance:+.1f}% QoQ (threshold: {expected_variance*100:.0f}%)"
            )
        else:
            result.context = f"Could not compute variance: curr={curr_val}, prev={prev_val}"
    else:
        result.context = f"No prior quarter available for comparison"

    # Cross-metric check: if this is Other Income, check PBT too
    cross_metrics = {
        "Other Income": ["Profit before tax", "Net Profit"],
        "Net Profit": ["Operating Profit", "Other Income"],
        "Operating Profit": ["Sales", "Expenses"],
    }
    checks_for = cross_metrics.get(metric, [])
    if checks_for:
        cross = {}
        for cm in checks_for:
            cm_data = _fuzzy_find_metric(quarterly_data, cm)
            if cm_data and quarter in cm_data and q_idx > 0:
                cm_curr = cm_data.get(quarter)
                cm_prev = cm_data.get(quarters[q_idx - 1])
                if cm_curr is not None and cm_prev is not None and cm_prev != 0:
                    cm_change = round(((cm_curr - cm_prev) / abs(cm_prev)) * 100, 2)
                    cross[cm] = {"current": cm_curr, "previous": cm_prev, "change_pct": cm_change}
        if cross:
            result.cross_metric_check = cross

    return result


# ── Auto-Scan ─────────────────────────────────────────────────────────────────

def auto_scan_anomalies(
    quarterly_data: dict,
    threshold: float = 0.30,
    metrics: list[str] = None,
) -> list[AnomalyVerification]:
    """
    Scan all metrics for QoQ anomalies exceeding the threshold.
    Returns list of confirmed anomalies with full context.
    """
    if quarterly_data is None:
        return []

    trends = get_qoq_trends(quarterly_data, metrics)
    anomalies = []

    for metric, quarters in trends.items():
        for q in quarters:
            if q.qoq_change_pct is not None and abs(q.qoq_change_pct) > threshold * 100:
                verification = verify_quarterly_anomaly(
                    metric, q.quarter, threshold, quarterly_data
                )
                if verification.confirmed:
                    anomalies.append(verification)

    return anomalies
