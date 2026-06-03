import json
import logging
import asyncio
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from novus_v3.signals.schemas import Event, Signal, Impact, SignalPayload
from novus_v3.signals.sources import fetch_all_events
from core.llm_client import get_llm_client

logger = logging.getLogger(__name__)

MATERIALITY_THRESHOLD = 60

def _pre_filter_events(events: List[Event]) -> List[Event]:
    """
    Cheap Pre-Filter:
    1. Dedupes by title hashing (already handled partially by ID).
    2. Drops generic fluff via regex/keywords, except "Notice of Board Meeting".
    """
    filtered = []
    seen_titles = set()
    
    for e in events:
        title_lower = e.raw_title.lower()
        if title_lower in seen_titles:
            continue
            
        # Example heuristic: drop purely administrative items that aren't board meetings
        if "trading window" in title_lower or "closure of trading" in title_lower:
            continue
            
        seen_titles.add(title_lower)
        filtered.append(e)
        
    return filtered

async def _score_materiality_and_novelty(events: List[Event], ticker: str, financial_context: str) -> List[Signal]:
    """
    Runs the LLM over the pre-filtered events to score materiality and assess novelty.
    """
    if not events:
        return []
        
    llm = get_llm_client(use_r1=False)
    
    events_json = [
        {"id": e.id, "title": e.raw_title, "summary": e.raw_summary, "source": e.source_name, "tier": e.source_tier}
        for e in events
    ]
    
    prompt = f"""You are a Signal Intelligence analyst for {ticker}.
Evaluate these incoming news/filings for materiality (0-100 score). Suppress noise.
Determine if the event is 'novel' (new information) or 'stale' (already priced in/known).

Baseline Context:
{financial_context}

Events:
{json.dumps(events_json, indent=2)}

Output JSON matching this exact array of objects (one per event):
[
  {{
    "event_id": "id1",
    "category": "regulatory|earnings|management|litigation|M&A|capital_allocation|macro_sector|other",
    "direction": "positive|negative|neutral",
    "materiality_score": 85,
    "confidence": "high|medium|low",
    "time_horizon": "immediate|quarters|structural",
    "is_novel": true,
    "summary": "1-sentence distillation of the true material impact."
  }}
]
"""
    try:
        response = await asyncio.to_thread(llm.call_simple, "Output valid JSON only.", prompt)
        clean = response.strip()
        if '```json' in clean:
            clean = clean.split('```json', 1)[1].rsplit('```', 1)[0]
        elif '```' in clean:
            clean = clean.split('```', 1)[1].rsplit('```', 1)[0]
            
        scored_data = json.loads(clean.strip())
        
        signals = []
        for s in scored_data:
            if s.get("materiality_score", 0) >= MATERIALITY_THRESHOLD:
                # Find the matching event to pull its tier
                matching_event = next((e for e in events if e.id == s["event_id"]), None)
                if not matching_event: continue
                
                confidence = s.get("confidence", "medium")
                # Cap confidence for Tier 2
                if matching_event.source_tier == 2 and confidence == "high":
                    confidence = "medium"
                    
                signals.append(Signal(
                    id=f"sig_{matching_event.id}",
                    event_ids=[matching_event.id],
                    category=s["category"],
                    direction=s["direction"],
                    materiality_score=s["materiality_score"],
                    confidence=confidence,
                    time_horizon=s.get("time_horizon", "immediate"),
                    highest_source_tier=matching_event.source_tier,
                    as_of=datetime.utcnow(),
                    is_novel=s.get("is_novel", True),
                    summary=s.get("summary", "")
                ))
        return signals
    except Exception as e:
        logger.warning(f"Materiality scoring failed: {e}")
        return []

async def _map_thesis_impact(signals: List[Signal], pm_thesis: dict, ticker: str) -> List[Impact]:
    """
    Maps the highly material signals against the fresh PM Synthesis thesis.
    Uses Stable IDs for Kill Criteria.
    """
    if not signals:
        return []
        
    llm = get_llm_client(use_r1=False)
    
    signals_json = [s.model_dump() for s in signals]
    
    thesis_context = {
        "bull_case": pm_thesis.get("bull_case", []),
        "bear_case": pm_thesis.get("bear_case", []),
        "kill_criteria": pm_thesis.get("kill_criteria", []),
        "upside_triggers": pm_thesis.get("upside_triggers", [])
    }
    
    prompt = f"""You are the Lead Portfolio Manager mapping live intelligence for {ticker} against your freshly written thesis.

Thesis Drivers & Criteria:
{json.dumps(thesis_context, indent=2)}

Live Signals to Map:
{json.dumps(signals_json, default=str, indent=2)}

For each signal, analyze how it impacts the thesis. If it triggers a kill criterion, output its EXACT 'id' (e.g. 'kc_1').
Output JSON matching this exact array of objects:
[
  {{
    "signal_id": "sig_123",
    "affected_thesis_drivers": ["Description of which bull/bear pillar is affected"],
    "direction": "positive|negative|neutral",
    "qualitative_magnitude": "Minor|Moderate|Severe|Transformative",
    "horizon": "Immediate|1-2 Quarters|Structural",
    "what_to_watch": "What is the next data point to verify this?",
    "confirms_or_contradicts_thesis": "confirms|contradicts|neutral",
    "triggers_kill_criterion_id": "kc_1" // or null if none triggered,
    "confidence": "high|medium|low",
    "evidence_event_ids": ["id1"]
  }}
]
"""
    try:
        response = await asyncio.to_thread(llm.call_simple, "Output valid JSON only.", prompt)
        clean = response.strip()
        if '```json' in clean:
            clean = clean.split('```json', 1)[1].rsplit('```', 1)[0]
        elif '```' in clean:
            clean = clean.split('```', 1)[1].rsplit('```', 1)[0]
            
        impact_data = json.loads(clean.strip())
        
        impacts = []
        for i in impact_data:
            impacts.append(Impact(
                signal_id=i["signal_id"],
                affected_thesis_drivers=i.get("affected_thesis_drivers", []),
                direction=i.get("direction", "neutral"),
                qualitative_magnitude=i.get("qualitative_magnitude", "Moderate"),
                horizon=i.get("horizon", "Immediate"),
                what_to_watch=i.get("what_to_watch", ""),
                confirms_or_contradicts_thesis=i.get("confirms_or_contradicts_thesis", "neutral"),
                triggers_kill_criterion_id=i.get("triggers_kill_criterion_id"),
                confidence=i.get("confidence", "medium"),
                evidence_event_ids=i.get("evidence_event_ids", [])
            ))
        return impacts
    except Exception as e:
        logger.warning(f"Thesis impact mapping failed: {e}")
        return []

async def run_signal_pipeline(ticker: str, financial_context: str) -> Tuple[List[Signal], List[str]]:
    """Phase A & B: Fetch, pre-filter, and score signals."""
    logger.info(f"Starting Signal Pipeline Phase A/B for {ticker}")
    raw_events, unavailable_sources = await fetch_all_events(ticker)
    
    filtered_events = _pre_filter_events(raw_events)
    
    signals = await _score_materiality_and_novelty(filtered_events, ticker, financial_context)
    
    return signals, unavailable_sources

async def run_impact_mapping(signals: List[Signal], pm_thesis: dict, ticker: str) -> List[Impact]:
    """Phase D: Map scored signals against final thesis."""
    logger.info(f"Starting Signal Pipeline Phase D for {ticker}")
    impacts = await _map_thesis_impact(signals, pm_thesis, ticker)
    return impacts
