"""
novus_v3/orchestrator/cio_v3.py — v3 CIO Orchestrator

Coordinates all v3 agents with:
  0. Lead Analyst Briefing: Generates dynamic, context-aware frameworks for each agent based on client mandate and macro reality.
  1. Parallel execution of independent agents using the dynamic frameworks.
  2. Data routing: quant agents get structured data, LLM agents get tools.
  3. Reflection: high-severity findings re-trigger relevant agents.
  4. Conflict detection: cross-check agent findings for contradictions.
  5. Synthesis: PM agent merges everything into a single thesis.
  6. Full audit trail preserved for every step.
"""

import json
import time
import asyncio
import re
from typing import Optional, Callable
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

# Dedicated ThreadPool to ensure 10 agents can run simultaneously 
# without queuing behind other default executor tasks.
_agent_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="AgentPool")

from core.llm_client import LLMClient, get_llm_client
from core.agent_base_v3 import AuditTrail, AgentV3
from core.memory import get_memory
from agents.all_agents import (
    ForensicInvestigatorV3,
    NarrativeDecoderV3,
    MoatArchitectV3,
    CapitalAllocatorV3,
    ManagementQualityV3,
    ForensicQuantV3,
    PMSynthesisV3,
    CriticAgentV3,
    ALL_AGENTS,
)


_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_month_year(label: str) -> Optional[tuple[int, int]]:
    """Parse a Screener column header like 'Mar 2024' or 'Dec 2025' into (month, year)."""
    if not label:
        return None
    m = re.match(r"\s*([A-Za-z]{3})[a-z]*\s+(\d{4})\s*$", label)
    if not m:
        return None
    month = _MONTH_ABBR.get(m.group(1).lower())
    if not month:
        return None
    return month, int(m.group(2))


def _indian_fiscal_period_from_month_year(month: int, year: int, annual: bool = False) -> str:
    """Given a calendar (month, year) point, return an Indian fiscal period string.

    Indian FY runs Apr-Mar. FY26 = Apr 2025 -> Mar 2026.
    Quarters: Apr-Jun=Q1, Jul-Sep=Q2, Oct-Dec=Q3, Jan-Mar=Q4.
    """
    fy_year = year + 1 if month >= 4 else year
    fy_yy = fy_year % 100
    if annual:
        return f"FY{fy_yy:02d}"
    quarter = ((month - 4) % 12) // 3 + 1
    return f"Q{quarter}_FY{fy_yy:02d}"


def _infer_fiscal_period(financial_tables: dict) -> str:
    """Derive a fiscal_period string from the most recent column across tables.

    Priority:
      1. Latest quarterly_results column -> 'Q{n}_FY{yy}'
      2. Latest annual column (profit_loss / balance_sheet) -> 'FY{yy}'
      3. Fallback to current calendar-derived annual period.
    """
    quarterly = financial_tables.get("quarterly_results") or {}
    if isinstance(quarterly, dict) and quarterly:
        parsed = [(_parse_month_year(k), k) for k in quarterly.keys()]
        parsed = [(my, k) for my, k in parsed if my is not None]
        if parsed:
            parsed.sort(key=lambda p: (p[0][1], p[0][0]))
            month, year = parsed[-1][0]
            return _indian_fiscal_period_from_month_year(month, year, annual=False)

    for tbl_name in ("profit_loss", "balance_sheet", "cash_flow"):
        tbl = financial_tables.get(tbl_name) or {}
        if not isinstance(tbl, dict) or not tbl:
            continue
        parsed = [(_parse_month_year(k), k) for k in tbl.keys()]
        parsed = [(my, k) for my, k in parsed if my is not None]
        if parsed:
            parsed.sort(key=lambda p: (p[0][1], p[0][0]))
            month, year = parsed[-1][0]
            return _indian_fiscal_period_from_month_year(month, year, annual=True)

    from datetime import datetime as _dt
    today = _dt.utcnow()
    return _indian_fiscal_period_from_month_year(today.month, today.year, annual=True)


from novus_v3.signals.pipeline import run_signal_pipeline, run_impact_mapping
from novus_v3.signals.schemas import SignalPayload

@dataclass
class OrchestratorState:
    ticker: str
    sector: str
    query: str
    fiscal_year: str = ""
    fiscal_period: str = ""  # e.g. "Q3_FY26" | "FY25" — set at run_pipeline entry

    client_profile: str = "Standard Institutional Mandate: Focus on sustainable growth, reasonable valuations, and clean accounting."
    macro_context: str = "Neutral macroeconomic environment."
    agent_frameworks: dict = field(default_factory=dict) 

    document_text: str = ""
    financial_tables: dict = field(default_factory=dict)
    extraction_signals: dict = field(default_factory=dict)

    wacc: float = 0.12
    terminal_growth: float = 0.05
    market_cap: Optional[float] = None

    agent_trails: dict[str, AuditTrail] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    
    data_ingestion_completeness: float = 1.0

    final_thesis: Optional[AuditTrail] = None
    final_report: str = ""
    signal_payload: Optional[SignalPayload] = None
    price_story: Optional[dict] = None  # Price Action Dossier (series + episodes) for UI + PM


EXECUTION_PHASES = [
    {
        "phase": "investigation",
        "parallel": True,
        "agents": [
            "forensic_quant",          
            "forensic_investigator",   
            "narrative_decoder",       
            "moat_architect",          
            "capital_allocator",       
            "management_quality",      
        ],
    },
    {
        "phase": "reflection",
        "parallel": False,
        "agents": [],   
    },
    {
        "phase": "verification",
        "parallel": False,
        "agents": ["critic_agent"],
    },
    {
        "phase": "synthesis",
        "parallel": False,
        "agents": ["pm_synthesis"],
    },
]


def _load_memory_with_validation(agent_name: str, ticker: str, fiscal_period: str, state: OrchestratorState) -> str:
    import time
    mem = get_memory()
    for attempt in range(3):
        try:
            result = mem.load_relevant_memories(
                agent_name=agent_name,
                ticker=ticker,
                target_fiscal_period=fiscal_period,
            )
            # Validation: if empty, check if we actually have rows.
            if not result:
                with mem._connect() as conn:
                    row = conn.execute("SELECT COUNT(*) as n FROM agent_mistakes WHERE ticker=? AND agent_name=?", (ticker, agent_name)).fetchone()
                    if row and row["n"] > 0:
                        # Should not be empty, retry
                        time.sleep(1)
                        continue
            return result or ""
        except Exception as e:
            from core.memory import DataRetrievalException
            if isinstance(e, DataRetrievalException) or attempt == 2:
                print(f"> [CIO] ⚠️ Memory retrieval failed for {agent_name}: {e}")
                state.data_ingestion_completeness = max(0.0, state.data_ingestion_completeness - 0.2)
                return ""
            time.sleep(1)
    
    state.data_ingestion_completeness = max(0.0, state.data_ingestion_completeness - 0.2)
    return ""

def _fy_label_to_column(fy_label: str) -> str:
    """Map 'FY24' -> 'Mar 2024' (Screener annual column convention)."""
    try:
        yy = int(fy_label.replace("FY", ""))
        return f"Mar 20{yy:02d}"
    except (ValueError, AttributeError):
        return ""


def _episode_fundamental_backdrop(episode: dict, financial_tables: dict) -> str:
    """One-line revenue/PAT delta across the fiscal years an episode spans."""
    pl = (financial_tables or {}).get("profit_loss") or {}
    fys = episode.get("fiscal_years") or []
    if not isinstance(pl, dict) or len(fys) < 2:
        return ""

    def _metric(fy: str, names: tuple[str, ...]) -> Optional[float]:
        col = pl.get(_fy_label_to_column(fy)) or {}
        for n in names:
            v = col.get(n)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    parts = []
    for label, names in (("Revenue", ("Sales", "Revenue", "Sales\u00a0")), ("PAT", ("Net Profit", "Profit after tax"))):
        start_v = _metric(fys[0], names)
        end_v = _metric(fys[-1], names)
        if start_v and end_v:
            chg = (end_v - start_v) / abs(start_v) * 100
            parts.append(f"{label} \u20b9{start_v:,.0f}Cr \u2192 \u20b9{end_v:,.0f}Cr ({'+' if chg >= 0 else ''}{chg:.0f}%)")
    if not parts:
        return ""
    return f"   Fundamental backdrop {fys[0]}\u2192{fys[-1]}: " + ", ".join(parts)


def _build_price_dossier_injection(ticker: str, price_story: dict, financial_tables: dict) -> str:
    """Assemble the PM mandate block: dossier + per-episode fundamentals + filing context."""
    blocks = ["\n\n" + price_story["dossier"]]

    episodes = price_story.get("episodes") or []
    context_lines = []
    for ep in episodes:
        backdrop = _episode_fundamental_backdrop(ep, financial_tables)
        if backdrop:
            context_lines.append(f"- {ep['type'].upper()} {ep['start']} \u2192 {ep['end']}:\n{backdrop}")

    # Filing context for the 3 largest episodes — fiscal-year-filtered RAG so the
    # PM gets period-correct narrative evidence (no temporal bleed).
    try:
        from rag_engine import query as _rag_query
        biggest = sorted(episodes, key=lambda e: abs(e.get("change_pct", 0)), reverse=True)[:3]
        for ep in biggest:
            try:
                hits = _rag_query(
                    ticker=ticker,
                    question=f"{ticker} major developments, guidance changes, regulatory actions, demand environment",
                    top_k=2,
                    doc_type_filter=["concall_transcript", "annual_report"],
                    target_fiscal_year=ep.get("fiscal_years") or None,
                )
                snippets = [
                    h["text"][:400].replace("\n", " ").strip()
                    for h in (hits or []) if h.get("chunk_id")
                ]
                if snippets:
                    context_lines.append(
                        f"- Filing context for {ep['type'].upper()} {ep['start']} \u2192 {ep['end']} "
                        f"({', '.join(ep.get('fiscal_years', []))}):\n   " + "\n   ".join(snippets)
                    )
            except Exception as e:
                print(f"> [CIO] \u26a0\ufe0f Price-episode RAG context failed: {e}")
    except Exception as e:
        print(f"> [CIO] \u26a0\ufe0f RAG unavailable for price dossier context: {e}")

    if context_lines:
        blocks.append("\nEPISODE CONTEXT (verified fundamentals + period-matched filing excerpts):")
        blocks.extend(context_lines)

    return "\n".join(blocks)


async def _generate_dynamic_frameworks(state: OrchestratorState, llm: LLMClient) -> dict:
    prompt = f"""You are the Director of Research for an Indian Equity Fund.
Target Company: {state.ticker} ({state.sector})
Client Mandate: {state.client_profile}
Current India Macro Reality: {state.macro_context}
Specific User Query: {state.query}

We are dispatching 5 qualitative agents to analyze this company's filings. 
Based entirely on the target sector, the macroeconomic context, and the client's specific mandate, write a strict 2-3 sentence 'custom_framework' (focus area) for EACH agent.

Rule: If the client is conservative, instruct the forensic agent to lower materiality thresholds. If the macro is inflationary, instruct the moat architect to heavily scrutinize pricing power and raw material pass-through. Give specific, tailored directions.

Output valid JSON ONLY matching this exact structure:
{{
  "forensic_investigator": "focus instructions...",
  "narrative_decoder": "focus instructions...",
  "moat_architect": "focus instructions...",
  "capital_allocator": "focus instructions...",
  "management_quality": "focus instructions..."
}}"""

    try:
        response = await asyncio.to_thread(
            llm.call_simple,
            "You are a Lead Analyst at a top-tier institutional equity fund. Output valid JSON only.",
            prompt,
        )
        clean = response.strip()
        if '```json' in clean:
            clean = clean.split('```json', 1)[1].rsplit('```', 1)[0]
        elif '```' in clean:
            clean = clean.split('```', 1)[1].rsplit('```', 1)[0]
            
        frameworks = json.loads(clean.strip())
        return frameworks
    except Exception as e:
        print(f"> [CIO] ⚠️ Lead Analyst failed to generate dynamic frameworks: {e}")
        return {}


async def run_pipeline(
    ticker: str,
    document_text: str,
    financial_tables: dict,
    sector: str,
    extraction_signals: dict,
    query: str = "",
    client_profile: str = "Standard Institutional Mandate",
    macro_context: str = "Neutral macroeconomic environment",
    wacc: float = 0.12,
    terminal_growth: float = 0.05,
    market_cap: float = None,
    progress_callback: Callable = None,
    llm: LLMClient = None,
) -> OrchestratorState:
    
    # ── Dual LLM Routing ──
    # V3: Fast, structured tool-calling — for all ReAct investigation agents
    # R1: Deep chain-of-thought reasoning — exclusively for PM Synthesis
    v3_llm = get_llm_client(use_r1=False)
    r1_llm = get_llm_client(use_r1=True)
    print(f"> [CIO] Model routing: ReAct agents → V3 | PM Synthesis → R1")

    inferred_period = _infer_fiscal_period(financial_tables or {})
    print(f"> [CIO] Fiscal period for this run: {inferred_period}")

    state = OrchestratorState(
        ticker=ticker,
        sector=sector,
        query=query,
        client_profile=client_profile,
        macro_context=macro_context,
        document_text=document_text,
        financial_tables=financial_tables,
        extraction_signals=extraction_signals,
        wacc=wacc,
        terminal_growth=terminal_growth,
        market_cap=market_cap,
        fiscal_period=inferred_period,
    )

    # ── PHASE A & B: KICK OFF SIGNAL PIPELINE CONCURRENTLY ──
    financial_context = f"Revenue: {financial_tables.get('profit_loss', {}).get('Sales', 'N/A')}\n" \
                        f"Target Period: {inferred_period}"
    signal_task = asyncio.create_task(run_signal_pipeline(ticker, financial_context))

    # ── PRICE ACTION DOSSIER: fetch in parallel with the agents (fail-soft) ──
    from core.price_story import fetch_price_story
    price_story_task = asyncio.create_task(asyncio.to_thread(fetch_price_story, ticker))

    if progress_callback:
        progress_callback("lead_analyst_planning", [], [])
        
    state.agent_frameworks = await _generate_dynamic_frameworks(state, v3_llm)
    
    from core.sector_archetypes import get_guardrails
    try:
        archetype_guardrails = get_guardrails(sector, fuzzy=True)
        if archetype_guardrails:
            guardrail_text = f"\n\n[MANDATORY SECTOR GUARDRAILS ({sector.upper()})]:\n{archetype_guardrails}"
            for agent_name in state.agent_frameworks:
                state.agent_frameworks[agent_name] += guardrail_text
    except Exception as e:
        print(f"> [CIO] Could not inject sector archetypes for {sector}: {e}")
        
    print(f"> [CIO] Lead Analyst generated frameworks for {len(state.agent_frameworks)} agents.")

    phase1 = EXECUTION_PHASES[0]
    phase1_agents = phase1["agents"].copy()

    # ── STAGED BLACKBOARD: Run Quant First ──
    if "forensic_quant" in phase1_agents:
        if progress_callback:
            progress_callback("investigation", ["forensic_quant"], [])
        await _run_agents_parallel(state, ["forensic_quant"], v3_llm, progress_callback)
        
        # Cross-pollinate the anomalies into the Prompt Composer's dynamic mandate
        quant_trail = state.agent_trails.get("forensic_quant")
        if quant_trail and quant_trail.findings:
            anomaly = quant_trail.findings.get("anomaly_flag")
            if anomaly:
                alert = (
                    f"\n\n## CRITICAL QUANT ALERT\n"
                    f"The Quantitative engine flagged an anomaly: {anomaly}\n"
                    f"Prioritize investigating this phenomenon."
                )
                for name in phase1_agents:
                    if name != "forensic_quant":
                        state.agent_frameworks[name] = state.agent_frameworks.get(name, "") + alert
                        
        phase1_agents.remove("forensic_quant")

    if phase1_agents:
        if progress_callback:
            progress_callback("investigation", phase1_agents, list(state.agent_trails.keys()))
        await _run_agents_parallel(state, phase1_agents, v3_llm, progress_callback)

    reflection_agents = _determine_reflection_needs(state)
    if reflection_agents:
        if progress_callback:
            progress_callback("reflection", reflection_agents, list(state.agent_trails.keys()))
        await _run_agents_parallel(state, reflection_agents, v3_llm, progress_callback)

    if progress_callback:
        progress_callback("conflict_check", [], list(state.agent_trails.keys()))
    state.conflicts = _detect_conflicts(state)

    # ═══════════════════════════════════════════════════════════════════
    # PHASE 3: VERIFICATION — Critic Agent scrubs all findings
    # ═══════════════════════════════════════════════════════════════════
    if progress_callback:
        progress_callback("verification", ["critic_agent"], list(state.agent_trails.keys()))

    # Extract current findings from all completed agents
    peer_findings = {}
    for name, trail in state.agent_trails.items():
        if trail.findings:
            peer_findings[name] = trail.findings
    if state.conflicts:
        peer_findings["_conflicts"] = state.conflicts

    print(f"> [CIO] Dispatching Critic Agent to verify {len(peer_findings)} agent outputs...")

    critic_mandate = state.agent_frameworks.get("critic_agent", "Verify every hard metric against source data.")
    try:
        critic_memory = _load_memory_with_validation(
            agent_name="critic_agent",
            ticker=ticker,
            fiscal_period=state.fiscal_period,
            state=state,
        )
        if critic_memory:
            critic_mandate += critic_memory
    except Exception as e:
        print(f"> [CIO] ⚠️ Memory injection failed for critic_agent: {e}")

    critic = CriticAgentV3()
    critic_trail = await asyncio.to_thread(
        critic.execute,
        ticker=ticker,
        document_text=document_text,
        financial_tables=financial_tables,
        sector=sector,
        extraction_signals=extraction_signals,
        peer_findings=peer_findings,
        llm=v3_llm,
        dynamic_mandate=critic_mandate,
        fiscal_period=state.fiscal_period,
    )
    state.agent_trails["critic_agent"] = critic_trail

    # ── Persist critic corrections into the memory layer (learn from today's run) ──
    # store_corrections also auto-fires narrative contradiction detection, so any
    # alpha signals produced here are injected into PM Synthesis below.
    try:
        mem = get_memory()
        if critic_trail.findings:
            result = mem.store_corrections(
                critic_trail.findings,
                ticker,
                fiscal_period=state.fiscal_period,
            )
            print(
                f"> [CIO] 🧠 Memory: {result.get('mistakes_written', 0)} mistakes, "
                f"{result.get('gaps_upserted', 0)} gaps, "
                f"{result.get('inconsistencies_found', 0)} new narrative inconsistencies."
            )
        # Also persist each agent's self-reported data gaps
        for name, trail in state.agent_trails.items():
            if name == "critic_agent":
                continue
            if trail.data_gaps:
                mem.store_agent_data_gaps(
                    name,
                    ticker,
                    trail.data_gaps,
                    fiscal_period=state.fiscal_period,
                )
    except Exception as e:
        print(f"> [CIO] ⚠️ Memory store_corrections failed: {e}")

    # Extract corrections from the Critic's output
    critic_corrections = []
    critic_status = "UNKNOWN"
    if critic_trail.findings:
        critic_corrections = critic_trail.findings.get("corrections", [])
        critic_status = critic_trail.findings.get("verification_status", "UNKNOWN")
    
    print(f"> [CIO] Critic Agent: {len(critic_corrections)} corrections. Status: {critic_status}")

    # ── VERIFICATION GATE: timeline rejection must not flow into the PM ──
    # When the critic rejects on chronological hallucination, deterministically
    # scrub the offending causal blocks from peer findings BEFORE synthesis and
    # force the PM to acknowledge the rejection. Specialist JSON never reaches
    # the PM uncorrected after a rejection.
    timeline_rejection_note = ""
    if critic_status == "REJECTED_TIMELINE_HALLUCINATION":
        rejection_reason = (critic_trail.findings or {}).get("rejection_reason", "Chronological inconsistency detected.")
        print(f"> [CIO] 🚨 VERIFICATION GATE: {rejection_reason} — scrubbing causal blocks before PM synthesis.")
        from utils.temporal_logic import verify_chronology

        def _scrub_bad_chronology(node):
            """Recursively remove causal blocks whose dates fail chronology."""
            removed = []
            if isinstance(node, dict):
                if "cause_date" in node and "effect_date" in node:
                    if not verify_chronology(node["cause_date"], node["effect_date"]):
                        removed.append(f"{node.get('cause_date')} -> {node.get('effect_date')}")
                        node.clear()
                        node["removed_by_auditor"] = "Causal claim removed: failed chronological verification."
                        return removed
                for v in node.values():
                    removed.extend(_scrub_bad_chronology(v))
            elif isinstance(node, list):
                for item in node:
                    removed.extend(_scrub_bad_chronology(item))
            return removed

        removed_chains = []
        for name, trail in state.agent_trails.items():
            if name == "critic_agent" or not trail.findings:
                continue
            removed_chains.extend(_scrub_bad_chronology(trail.findings))

        timeline_rejection_note = (
            "\n\n## AUDITOR TIMELINE REJECTION (NON-NEGOTIABLE)\n"
            f"The Auditor REJECTED the specialist findings for chronological hallucination: {rejection_reason}\n"
            f"The offending causal chains have been removed: {removed_chains or ['(see rejection reason)']}\n"
            "You MUST NOT reconstruct or reference these cause→effect claims. "
            "Treat the affected narratives as UNVERIFIED and lower your conviction accordingly."
        )

    # ═══════════════════════════════════════════════════════════════════
    # PHASE 4: SYNTHESIS — PM merges everything, with Critic overrides
    # ═══════════════════════════════════════════════════════════════════
    if progress_callback:
        progress_callback("synthesis", ["pm_synthesis"], list(state.agent_trails.keys()))

    # ── THE HARD OVERRIDE: Force Reality on the Pipeline ──
    if critic_trail and critic_trail.findings and isinstance(critic_trail.findings, dict):
        corrections = critic_trail.findings.get("corrections", [])
        
        for correction in corrections:
            agent_name = correction.get("agent_name")
            original_claim = str(correction.get("original_claim", ""))
            verified_fact = str(correction.get("verified_fact", ""))
            
            # Physically replace the string in the agent's output dictionary
            if agent_name in state.agent_trails and original_claim and verified_fact:
                try:
                    agent_finding_str = json.dumps(state.agent_trails[agent_name].findings)
                    if original_claim in agent_finding_str:
                        scrubbed_str = agent_finding_str.replace(original_claim, verified_fact)
                        state.agent_trails[agent_name].findings = json.loads(scrubbed_str)
                        print(f"> [CIO] 🚨 CRITIC OVERRIDE: Scrubbed '{original_claim}' -> '{verified_fact}' in {agent_name}")
                except Exception as e:
                    print(f"> [CIO] ⚠️ Failed to apply critic correction: {e}")
    # ────────────────────────────────────────────────

    if progress_callback:
        progress_callback("synthesis", ["pm_synthesis"], list(state.agent_trails.keys()))

    # Build the final agent outputs 
    agent_outputs = {}
    for name, trail in state.agent_trails.items():
        # Exclude the Critic from the PM prompt since we already directly applied its corrections
        if trail.findings and name != "critic_agent":  
            agent_outputs[name] = trail.findings

    pm_signals = {**extraction_signals, "_agent_outputs": agent_outputs}

    # Inject Critic corrections into PM Synthesis dynamic mandate
    pm_mandate = state.agent_frameworks.get("pm_synthesis", "")

    try:
        pm_memory = _load_memory_with_validation(
            agent_name="pm_synthesis",
            ticker=ticker,
            fiscal_period=state.fiscal_period,
            state=state,
        )
        if pm_memory:
            pm_mandate += pm_memory
    except Exception as e:
        print(f"> [CIO] ⚠️ Memory injection failed for pm_synthesis: {e}")

    if timeline_rejection_note:
        pm_mandate += timeline_rejection_note

    # ── PRICE ACTION DOSSIER: ground the "stock story" in real market data ──
    try:
        state.price_story = await price_story_task
    except Exception as e:
        print(f"> [CIO] ⚠️ Price story fetch failed: {e}")
        state.price_story = None

    if state.price_story:
        dossier_block = await asyncio.to_thread(
            _build_price_dossier_injection, ticker, state.price_story, financial_tables
        )
        pm_mandate += dossier_block
        print(f"> [CIO] 📈 Price Action Dossier injected ({len(state.price_story.get('episodes', []))} episodes).")
    else:
        pm_mandate += (
            "\n\n## PRICE ACTION DOSSIER: UNAVAILABLE\n"
            "Market price history could not be retrieved for this run. "
            "OMIT the stock_story section entirely and note it as a data gap — "
            "do NOT narrate price moves from memory."
        )

    if critic_corrections:
        corrections_text = json.dumps(critic_corrections, indent=2, default=str)
        audit_injection = (
            "\n\n## CRITICAL AUDIT REPORT\n"
            "The following data points from the specialist agents contained errors "
            "and were corrected by the Auditor. You MUST use these verified facts "
            "in your final thesis, ignoring the erroneous claims:\n\n"
            f"```json\n{corrections_text}\n```\n\n"
            "Any metric listed as CORRECTED above supersedes the original agent claim. "
            "Any metric listed as FLAGGED_AS_DATA_GAP must be acknowledged as unverified "
            "in your report — do NOT present it as fact."
        )
        pm_mandate += audit_injection
    elif critic_status == "CLEARED":
        pm_mandate += "\n\n## AUDIT STATUS: ALL CLEAR\nThe Auditor has verified all material claims. No corrections needed."

    pm = PMSynthesisV3()
    thesis_trail = await asyncio.to_thread(
        pm.execute,
        ticker=ticker,
        document_text=document_text,  
        financial_tables=financial_tables,
        sector=sector,
        extraction_signals=pm_signals,
        llm=r1_llm,  # R1 for deep reasoning synthesis
        dynamic_mandate=pm_mandate,
        fiscal_period=state.fiscal_period,
    )
    
    state.agent_trails["pm_synthesis"] = thesis_trail
    state.final_thesis = thesis_trail
    raw_report = thesis_trail.to_analyst_note() if hasattr(thesis_trail, 'to_analyst_note') else str(thesis_trail.findings)
    state.final_report = _sanitize_final_report(raw_report)

    # ── PHASE C & D: SYNC SIGNAL PIPELINE AND MAP IMPACTS ──
    try:
        signals, unavailable_sources, events = await signal_task
        if signals:
            impacts = await run_impact_mapping(signals, thesis_trail.findings, ticker)
        else:
            impacts = []
        state.signal_payload = SignalPayload(
            signals=signals, 
            impacts=impacts,
            events=events,
            unavailable_sources=unavailable_sources
        )
    except Exception as e:
        print(f"> [CIO] ⚠️ Signal Pipeline failed: {e}")
        state.signal_payload = SignalPayload(signals=[], impacts=[], events=[], unavailable_sources=["Internal Pipeline Error"])

    if progress_callback:
        progress_callback("complete", [], list(state.agent_trails.keys()))

    return state


async def _run_agents_parallel(
    state: OrchestratorState,
    agent_names: list[str],
    llm: LLMClient,
    progress_callback: Callable = None,
):
    async def _run_one(name: str) -> tuple[str, AuditTrail]:
        agent_cls = ALL_AGENTS.get(name)
        if agent_cls is None:
            return name, AuditTrail(
                agent_name=name, ticker=state.ticker,
                data_gaps=[f"Agent '{name}' not found in registry"],
                confidence=0.0,
            )

        agent = agent_cls()
        loop = asyncio.get_running_loop()
        custom_mandate = state.agent_frameworks.get(name, "")

        # ── Inject learned memory from past runs ──
        try:
            memory_block = _load_memory_with_validation(
                agent_name=name,
                ticker=state.ticker,
                fiscal_period=state.fiscal_period,
                state=state,
            )
            if memory_block:
                custom_mandate = (custom_mandate or "") + memory_block
                print(f"> [CIO] 🧠 Memory injected into {name} mandate ({len(memory_block)} chars)")
        except Exception as e:
            print(f"> [CIO] ⚠️ Memory injection failed for {name}: {e}")

        try:
            if name == "forensic_quant":
                trail = await loop.run_in_executor(
                    _agent_executor,
                    lambda: agent.execute(
                        ticker=state.ticker,
                        financial_tables=state.financial_tables,
                        wacc=state.wacc,
                        terminal_growth=state.terminal_growth,
                        market_cap=state.market_cap,
                    ),
                )
            else:
                trail = await asyncio.wait_for(
                    loop.run_in_executor(
                        _agent_executor,
                        lambda: agent.execute(
                            ticker=state.ticker,
                            document_text=state.document_text,
                            financial_tables=state.financial_tables,
                            sector=state.sector,
                            extraction_signals=state.extraction_signals,
                            llm=llm,
                            dynamic_mandate=custom_mandate,
                            fiscal_period=state.fiscal_period,
                        ),
                    ),
                    timeout=120.0, 
                )
            return name, trail

        except asyncio.TimeoutError:
            return name, AuditTrail(
                agent_name=name, ticker=state.ticker,
                data_gaps=[f"Agent '{name}' timed out after 180s"],
                confidence=0.0,
            )
        except Exception as e:
            return name, AuditTrail(
                agent_name=name, ticker=state.ticker,
                data_gaps=[f"Agent '{name}' crashed: {e}"],
                confidence=0.0,
            )

    tasks = [_run_one(name) for name in agent_names]
    completed = set(state.agent_trails.keys())

    for coro in asyncio.as_completed(tasks):
        name, trail = await coro
        state.agent_trails[name] = trail
        completed.add(name)
        print(f"> [CIO] ✅ Agent {name} COMPLETED (conf: {trail.confidence})")

        if progress_callback:
            from utils.formatters import format_dict_as_markdown
            active = [n for n in agent_names if n not in completed]
            agent_outputs = {}
            for n, t in state.agent_trails.items():
                if t.findings:
                    agent_outputs[n] = "\n".join(format_dict_as_markdown(t.findings, indent=0))
                elif t.data_gaps:
                    agent_outputs[n] = "**Data Gaps:**\n" + "\n".join(f"- {g}" for g in t.data_gaps)
            
            progress_callback("investigation", active, list(completed), agent_outputs=agent_outputs)


def _determine_reflection_needs(state: OrchestratorState) -> list[str]:
    reflection_agents = []

    forensic_trail = state.agent_trails.get("forensic_investigator")
    if forensic_trail and forensic_trail.findings and isinstance(forensic_trail.findings, dict):
        high_severity = False
        for key in ["related_party_flags", "auditor_flags", "contingent_liabilities"]:
            items = forensic_trail.findings.get(key, [])
            if any(isinstance(f, dict) and f.get("severity") in ("HIGH", "CRITICAL") for f in items):
                high_severity = True
                break
        if high_severity:
            reflection_agents.append("forensic_quant")

    capital_trail = state.agent_trails.get("capital_allocator")
    if capital_trail and capital_trail.findings and isinstance(capital_trail.findings, dict):
        empire = capital_trail.findings.get("empire_building", {})
        if isinstance(empire, dict) and empire.get("unrelated_acquisitions"):
            reflection_agents.append("narrative_decoder")

    mgmt_trail = state.agent_trails.get("management_quality")
    if mgmt_trail and mgmt_trail.findings and isinstance(mgmt_trail.findings, dict):
        flags = mgmt_trail.findings.get("governance_flags", [])
        if isinstance(flags, list) and len(flags) >= 3:
            reflection_agents.append("forensic_investigator")

    return list(dict.fromkeys(reflection_agents)) 


def _detect_conflicts(state: OrchestratorState) -> list[dict]:
    conflicts = []

    quant = state.agent_trails.get("forensic_quant")
    forensic = state.agent_trails.get("forensic_investigator")

    if quant and forensic and isinstance(quant.findings, dict) and isinstance(forensic.findings, dict):
        ocf_ratio = quant.findings.get("ocf_ebitda_ratio")
        has_high_flags = any(
            isinstance(f, dict) and f.get("severity") in ("HIGH", "CRITICAL")
            for key in ("related_party_flags", "auditor_flags")
            for f in forensic.findings.get(key, [])
        )
        if isinstance(ocf_ratio, (int, float)) and ocf_ratio > 0.8 and has_high_flags:
            conflicts.append({
                "agents": ["forensic_quant", "forensic_investigator"],
                "severity": "MEDIUM",
                "description": f"Quant says strong cash quality (OCF/EBITDA={ocf_ratio:.0%}) but forensic agent found HIGH severity accounting flags.",
            })

    moat = state.agent_trails.get("moat_architect")
    narrative = state.agent_trails.get("narrative_decoder")

    if moat and narrative and isinstance(moat.findings, dict) and isinstance(narrative.findings, dict):
        moat_verdict = str(moat.findings.get("moat_durability", "")).upper()
        tone_shifts = narrative.findings.get("tone_shifts", [])
        
        has_bearish_shift = any(
            isinstance(t, dict) and ("cautious" in str(t.get("current_tone", "")).lower() or "challenging" in str(t.get("current_tone", "")).lower())
            for t in tone_shifts if isinstance(t, dict)
        )
        if moat_verdict in ("STRONG", "INTACT") and has_bearish_shift:
            conflicts.append({
                "agents": ["moat_architect", "narrative_decoder"],
                "severity": "MEDIUM",
                "description": f"Moat analysis says '{moat_verdict}' but management tone is actively deteriorating in concalls.",
            })

    return conflicts


# ═══════════════════════════════════════════════════════════════════════════
# Post-Processing: Final Report Sanitiser
# ═══════════════════════════════════════════════════════════════════════════

# Patterns that leak internal multi-agent architecture into the client-facing report
_AGENT_BLEED_PATTERNS = [
    # Direct agent name references (case-insensitive)
    (re.compile(r'\b(?:the\s+)?(?:forensic[\s_]?quant|narrative[\s_]?decoder|moat[\s_]?architect|capital[\s_]?allocator|management[\s_]?quality|forensic[\s_]?investigator|pm[\s_]?synthesis|critic[\s_]?agent)\s*(?:agent)?\s*(?:flags?|confirms?|notes?|shows?|indicates?|reveals?|suggests?|finds?|highlights?|warns?|reports?)', re.IGNORECASE),
     ''),
    # "Our [X] analysis shows" → just state the finding
    (re.compile(r'\b(?:our|the)\s+(?:forensic|narrative|moat|capital|management|quant(?:itative)?)\s+(?:analysis|assessment|review|audit|investigation|scan)\s+(?:shows?|reveals?|confirms?|indicates?|flags?|suggests?)\s+(?:that\s+)?', re.IGNORECASE),
     ''),
    # "According to our [agent_type]" patterns
    (re.compile(r'\b(?:according\s+to|based\s+on|as\s+per)\s+(?:our|the)\s+(?:forensic|narrative|moat|capital|management|quant(?:itative)?)\s+(?:analysis|agent|module|assessment|review)\s*,?\s*', re.IGNORECASE),
     ''),
]

# Conversational phrases → precise financial terminology
_VOCABULARY_FIXES = [
    (re.compile(r'\bdirect\s+contradiction\b', re.IGNORECASE), 'divergence'),
    (re.compile(r'\bcontradicts?\b', re.IGNORECASE), 'diverges from'),
    (re.compile(r'\bcash\s+is\s+(?:good|strong|healthy)\b', re.IGNORECASE), 'cash conversion is robust'),
    (re.compile(r'\bgrowth\s+is\s+slowing\b', re.IGNORECASE), 'growth rate is decelerating'),
    (re.compile(r'\bmasked\s+by\s+a\s+promising\s+narrative\b', re.IGNORECASE), 'diverges from underlying fundamentals'),
    (re.compile(r'\bthe\s+moat\s+is\s+weakening,\s*not\s+strengthening\b', re.IGNORECASE), 'moat durability is weakening'),
    (re.compile(r'\beradicating\s+narrative\s+bias\b', re.IGNORECASE), ''),
]

# Segmental conflation detector (warns but does not auto-fix — too risky)
_SEGMENT_CONFLATION = re.compile(
    r'(?:US|U\.S\.)\s+(?:injectables?|generics?|specialty|biosimilars?|OTC)\s+'
    r'(?:contributes?|accounts?\s+for|represents?|comprises?)\s+'
    r'(?:about\s+|approximately\s+|around\s+|~?\s*)?'
    r'(\d{1,3})%\s+(?:of\s+)?(?:total\s+)?(?:revenue|sales|top[\s-]?line)',
    re.IGNORECASE
)


def _sanitize_final_report(text: str) -> str:
    """Post-process the PM Synthesis output to strip agent bleed-through,
    fix conversational vocabulary, and flag segmental conflation.
    
    This is a safety net — the prompt already instructs the LLM to avoid
    these patterns, but LLMs are non-deterministic.
    """
    # 1. Strip agent bleed-through
    for pattern, replacement in _AGENT_BLEED_PATTERNS:
        text = pattern.sub(replacement, text)

    # 2. Fix conversational vocabulary
    for pattern, replacement in _VOCABULARY_FIXES:
        text = pattern.sub(replacement, text)

    # 3. Flag potential segmental conflation (annotate, don't delete)
    for match in _SEGMENT_CONFLATION.finditer(text):
        pct = match.group(1)
        if int(pct) > 30:  # Only flag large percentages likely to be geographic-level
            warning = f" [⚠️ Verify: this percentage may refer to total US geographic revenue, not the specific sub-segment]"
            text = text[:match.end()] + warning + text[match.end():]
            break  # Only annotate the first occurrence to avoid cluttering

    # 4. Clean up any double-spaces or orphaned punctuation from regex subs
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n +\n', '\n\n', text)
    text = re.sub(r'^\s*,\s*', '', text, flags=re.MULTILINE)

    return text


async def analyze(
    ticker: str,
    document_text: str,
    financial_tables: dict,
    sector: str,
    extraction_signals: dict = None,
    query: str = "",
    client_profile: str = "Standard Institutional Mandate",
    macro_context: str = "Neutral macroeconomic environment",
    wacc: float = 0.12,
    progress_callback: Callable = None,
) -> OrchestratorState:
    
    return await run_pipeline(
        ticker=ticker,
        document_text=document_text,
        financial_tables=financial_tables,
        sector=sector,
        extraction_signals=extraction_signals or {},
        query=query,
        client_profile=client_profile,
        macro_context=macro_context,
        wacc=wacc,
        progress_callback=progress_callback,
    )
