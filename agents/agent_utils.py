import re
from rag_engine import query as rag_query


def _safe_handler(fn):
    """Wrap a tool handler so failures give the LLM an exit ramp.
    
    Without this, a ReAct agent will retry failed tool calls in an infinite
    loop until MAX_ITERATIONS, wasting compute and delaying the report.
    The explicit instruction_to_agent directive tells V3 to move on.
    """
    def wrapper(**kwargs):
        try:
            return fn(**kwargs)
        except Exception as e:
            return {
                "error": str(e),
                "failed_args": kwargs,
                "instruction_to_agent": (
                    "This tool call FAILED. DO NOT retry with the same arguments. "
                    "Record this as a data gap in your findings and continue "
                    "investigating other areas."
                ),
            }
    return wrapper

def _fget(data: dict, *keys, default=None):
    """Fuzzy key lookup with NBSP normalization.
    
    Screener.in injects non-breaking spaces (\\xa0) and trailing '+' into
    its HTML table headers. This normalizes both the search keys and the
    dict keys before matching to prevent silent None returns that cascade
    into empty tool payloads and LLM hallucinations.
    """
    def _norm(s: str) -> str:
        return s.replace('\xa0', ' ').rstrip('+').strip().lower()
    
    # Build a normalized lookup map once
    norm_map = {_norm(dk): dv for dk, dv in data.items()}
    
    for k in keys:
        # 1. Exact match
        val = data.get(k)
        if val is not None:
            return val
        # 2. Normalized exact match
        nk = _norm(k)
        val = norm_map.get(nk)
        if val is not None:
            return val
        # 3. Substring match (normalized)
        for dk_norm, dv in norm_map.items():
            if nk in dk_norm or dk_norm in nk:
                return dv
    return default

def _reverse_dcf(mcap, fcf, wacc=0.12, tg=0.05, years=10):
    if mcap <= 0 or fcf <= 0 or wacc <= tg:
        return None
    lo, hi = 0.0, 0.50
    for _ in range(100):
        mid = (lo + hi) / 2
        pv = sum(fcf * (1 + mid) ** t / (1 + wacc) ** t for t in range(1, years + 1))
        tv = fcf * (1 + mid) ** years * (1 + tg) / (wacc - tg)
        pv += tv / (1 + wacc) ** years
        if pv < mcap:
            lo = mid
        else:
            hi = mid
        if abs(hi - lo) < 0.0001:
            break
    return round((lo + hi) / 2, 4)

def _cross_ref(tables, item_a, item_b, table):
    tbl = tables.get(table, {})
    years = sorted(tbl.keys())
    data = {"item_a": item_a, "item_b": item_b, "comparison": []}
    for i in range(1, len(years)):
        y0, y1 = years[i-1], years[i]
        va0 = _fuzzy(tbl.get(y0, {}), item_a)
        va1 = _fuzzy(tbl.get(y1, {}), item_a)
        vb0 = _fuzzy(tbl.get(y0, {}), item_b)
        vb1 = _fuzzy(tbl.get(y1, {}), item_b)
        row = {"year": y1}
        if isinstance(va0, (int,float)) and isinstance(va1, (int,float)) and va0:
            row["a_growth"] = round(((va1 - va0) / abs(va0)) * 100, 1)
        if isinstance(vb0, (int,float)) and isinstance(vb1, (int,float)) and vb0:
            row["b_growth"] = round(((vb1 - vb0) / abs(vb0)) * 100, 1)
        if "a_growth" in row and "b_growth" in row:
            row["gap_pp"] = round(row["a_growth"] - row["b_growth"], 1)
            row["diverging"] = abs(row["gap_pp"]) > 15
        data["comparison"].append(row)
    return data

def _fuzzy(data: dict, key: str):
    if key in data:
        return data[key]
    for k, v in data.items():
        if key.lower() in k.lower():
            return v
    return None

def _search_guidance(doc, topic, ticker=""):
    if ticker:
        results = rag_query(ticker, f"management guidance outlook expected target {topic}", top_k=5)
        if results:
            return [{"passage": r["text"][:800], "type": "guidance_rag", "score": r["relevance"]} for r in results]

    # Fallback to regex
    patterns = [
        rf"(?i)(?:we expect|guidance|outlook|target|aim to|going forward).*?{re.escape(topic)}",
        rf"(?i){re.escape(topic)}.*?(?:we expect|guidance|outlook|target)",
    ]
    results = []
    for pat in patterns:
        for m in re.finditer(pat, doc):
            start = max(0, m.start() - 100)
            end = min(len(doc), m.end() + 300)
            results.append({"passage": doc[start:end].strip(), "type": "guidance_regex"})
    return results[:5] or [{"passage": f"No guidance found for '{topic}'", "type": "none"}]

def _detect_hedging(doc, section, sector="General"):
    # Universal hedging phrases
    hedging_phrases = [
        "challenging environment", "one-time", "one time", "strategic investment",
        "going forward", "as I said", "let me clarify", "I think we need to",
        "it's too early to", "we'll have to wait", "difficult to predict",
        "cautiously optimistic", "calibrated approach", "headwinds",
        "sustainable profitable growth", "long-term value creation",
        "we remain confident", "broadly in line",
    ]
    
    # Sector-aware evasion phrases
    sector_lower = sector.lower()
    if any(k in sector_lower for k in ["fmcg", "consumer", "food", "personal care"]):
        hedging_phrases.extend([
            "controlling controllables", "premiumization journey",
            "right-sizing portfolio", "calibrated pricing",
            "investing behind brands", "grammage rationalization",
            "building distribution muscle",
        ])
    elif any(k in sector_lower for k in ["bank", "financial", "nbfc"]):
        hedging_phrases.extend([
            "granular monitoring", "proactive provisioning",
            "dispensation period", "restructured book",
            "elevated slippages", "one-off recovery",
        ])
    elif any(k in sector_lower for k in ["software", "it ", "technology"]):
        hedging_phrases.extend([
            "deal pipeline robust", "seasonal furloughs",
            "ramp-up delays", "vendor consolidation opportunity",
            "discretionary spending pause",
        ])
    
    text = doc
    if section == "qa_only":
        qa_idx = doc.lower().find("question and answer")
        if qa_idx == -1:
            qa_idx = doc.lower().find("q&a")
        if qa_idx > 0:
            text = doc[qa_idx:]
    found = []
    for phrase in hedging_phrases:
        count = text.lower().count(phrase)
        if count > 0:
            idx = text.lower().find(phrase)
            context = text[max(0, idx-100):idx+len(phrase)+200].strip()
            found.append({"phrase": phrase, "count": count, "context": context})
    found.sort(key=lambda x: -x["count"])
    return found[:15] or [{"phrase": "No hedging language detected", "count": 0}]

def _search_competitive(doc, topic, ticker=""):
    if ticker:
        results = rag_query(ticker, f"competitive advantage moat market share competitors {topic}", top_k=3)
        if results:
            return [{"passage": r["text"][:800], "score": r["relevance"], "source": "rag"} for r in results]

    # Fallback to BM25
    keywords = topic.lower().split()
    paras = re.split(r'\n\s*\n', doc)
    scored = []
    for p in paras:
        if len(p.strip()) < 30:
            continue
        lower = p.lower()
        score = sum(lower.count(k) for k in keywords if len(k) > 2)
        if score > 0:
            scored.append((score, p.strip()[:800]))
    scored.sort(key=lambda x: -x[0])
    return [{"passage": p, "score": s, "source": "regex"} for s, p in scored[:3]] or [{"passage": "Not found", "score": 0}]

def _search_capital(doc, topic, ticker=""):
    if ticker:
        results = rag_query(ticker, f"capital allocation capex dividend buyback acquisition {topic}", top_k=3)
        if results:
            return [{"passage": r["text"][:800], "score": r["relevance"], "source": "rag"} for r in results]
    return _search_competitive(doc, topic)  

def _search_governance(doc, topic, ticker=""):
    if ticker:
        results = rag_query(ticker, f"management board directors related party auditor {topic}", top_k=3)
        if results:
            return [{"passage": r["text"][:800], "score": r["relevance"], "source": "rag"} for r in results]
    return _search_competitive(doc, topic)
