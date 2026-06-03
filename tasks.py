
from utils.logger import get_logger
logger = get_logger(__name__)
# tasks.py — Novus FinLLM Multi-Agent Pipeline
"""
Restructured background task pipeline using the V3 MAS architecture.
"""

import json
import time
import hashlib
import asyncio
from rq import get_current_job

# --- Persistent Event Loop for RQ Workers ---
# Prevents macOS fork() + CoreFoundation crashes by reusing the loop footprint
_runner = asyncio.Runner()

from agents.extraction import run_extraction_pipeline
from rag_engine import ingest_documents, get_collection_stats, get_context_for_agent
from structured_data_fetcher import get_structured_data_fetcher
from cio_orchestrator import analyze, OrchestratorState


from utils.formatters import format_dict_as_markdown

def build_ui_payloads(st: OrchestratorState) -> tuple[dict, dict, dict | None, dict | None, dict]:
    """Single source of truth for UI payload construction from OrchestratorState (V3)."""
    a_outs = {}
    for name, trail in st.agent_trails.items():
        if trail.findings:
            md_lines = format_dict_as_markdown(trail.findings, indent=0)
            a_outs[name] = "\n".join(md_lines)
        elif trail.data_gaps:
            a_outs[name] = f"**Data Gaps:**\n" + "\n".join(f"- {g}" for g in trail.data_gaps)
        else:
            a_outs[name] = "> **ERROR:** Agent failed to produce valid findings. The LLM may have failed to format its response as JSON."

    kill_reasons = []
    warnings = []
    passed = True

    fi = st.agent_trails.get("forensic_investigator")
    if fi and fi.findings and isinstance(fi.findings, dict):
        fo = fi.findings
        for field in ["related_party_flags", "auditor_flags", "contingent_liabilities"]:
            for issue in fo.get(field, []):
                desc = issue.get("description", "") if isinstance(issue, dict) else str(issue)
                sev = issue.get("severity", "") if isinstance(issue, dict) else ""

                if str(sev).upper() in ("HIGH", "CRITICAL"):
                    passed = False
                    kill_reasons.append(f"{field.upper()}: {desc}")
                else:
                    warnings.append(f"{field.upper()}: {desc}")

    t_res = {"passed": passed, "kill_reasons": kill_reasons, "warnings": warnings}

    f_score = None
    fsa = st.agent_trails.get("forensic_quant")
    if fsa and fsa.findings:
        f_score = fsa.findings

    # ── Compute Real NLP Evasion Score ──
    evasion_data = _compute_evasion_score(st)

    # ── Agent Trails Summary for Charts ──
    agent_trails_summary = {}
    for name, trail in st.agent_trails.items():
        agent_trails_summary[name] = {
            "confidence": trail.confidence,
            "findings": trail.findings if trail.findings else None,
            "execution_time_s": trail.execution_time_s,
        }

    return a_outs, t_res, f_score, evasion_data, agent_trails_summary


def _compute_evasion_score(st: OrchestratorState) -> dict | None:
    """
    Compute a real 0-100 evasion score from the Narrative Decoder's structured findings.
    
    Scoring methodology:
      - Guidance misses with LOW credibility:   +15 each (max 45)
      - Tone shifts marked HIGH significance:   +10 each (max 30)
      - Analyst dodges (any):                   +8 each  (max 24)
      - Key phrases flagged:                    +3 each  (max 15)
      - Base score starts at 10 (benefit of doubt)
    
    Returns: { "score": int, "breakdown": {...}, "verdict": str }
    """
    nd = st.agent_trails.get("narrative_decoder")
    if not nd or not nd.findings or not isinstance(nd.findings, dict):
        return None

    findings = nd.findings
    score = 10  # base: benefit of the doubt
    breakdown = {
        "guidance_misses": 0,
        "tone_shifts": 0,
        "analyst_dodges": 0,
        "flagged_phrases": 0,
    }

    # 1. Guidance tracker — credibility misses
    for item in findings.get("guidance_tracker", []):
        cred = ""
        if isinstance(item, dict):
            cred = str(item.get("credibility", "")).upper()
        if "LOW" in cred:
            score += 15
            breakdown["guidance_misses"] += 1
        elif "MEDIUM" in cred:
            score += 7
            breakdown["guidance_misses"] += 1

    # 2. Tone shifts — significance
    for item in findings.get("tone_shifts", []):
        sig = ""
        if isinstance(item, dict):
            sig = str(item.get("significance", "")).upper()
        if "HIGH" in sig:
            score += 10
            breakdown["tone_shifts"] += 1
        elif "MEDIUM" in sig:
            score += 5
            breakdown["tone_shifts"] += 1

    # 3. Analyst dodges
    for item in findings.get("analyst_dodges", []):
        score += 8
        breakdown["analyst_dodges"] += 1

    # 4. Key phrases flagged
    for item in findings.get("key_phrases_flagged", []):
        score += 3
        breakdown["flagged_phrases"] += 1

    # Clamp to 0-100
    score = max(0, min(100, score))

    # Verdict
    if score >= 75:
        verdict = "HIGH EVASION — Management is likely deflecting on critical issues"
    elif score >= 45:
        verdict = "MODERATE EVASION — Some hedging detected, selective transparency"
    elif score >= 25:
        verdict = "LOW EVASION — Minor hedging, mostly transparent communication"
    else:
        verdict = "TRANSPARENT — Management communication appears forthcoming"

    return {"score": score, "breakdown": breakdown, "verdict": verdict}


PROGRESS_STAGES = [
    "extract_pdfs",
    "ingest_rag",
    "fetch_financials",
    "lead_analyst_planning",
    "investigation",
    "reflection",
    "conflict_check",
    "synthesis",
    "assemble",
    "complete"
]


def _update_progress(stage: str, extra: dict | None = None):
    job = get_current_job()
    if not job:
        return
    job.meta['stage'] = stage
    if 'stages' not in job.meta:
        job.meta['stages'] = PROGRESS_STAGES
    if extra:
        job.meta.update(extra)
    job.save_meta()


def generate_financial_report_from_rag(ticker):
    """
    RAG-Only Mode: Full Novus analysis using ONLY:
      1. Stored documents in ChromaDB (previously ingested)
      2. Screener.in financial data (auto-fetched)

    No PDF upload needed — everything comes from the vector store.
    """
    try:
        from rag_engine import query as rag_query

        # ═══════════════════════════════════════════════════════════════
        # CHECK RAG STORE
        # ═══════════════════════════════════════════════════════════════
        _update_progress('ingest_rag')
        stats = get_collection_stats(ticker)
        if stats['total_chunks'] == 0:
            raise ValueError(
                f"No documents found for ticker {ticker} in RAG store. "
                f"Please ingest documents first via /ingest_local."
            )
        logger.info(f"[RAG-Only] Found {stats['total_chunks']} chunks for {ticker}")

        # ═══════════════════════════════════════════════════════════════
        # BUILD SYNTHETIC TRANSCRIPT FROM RAG STORE
        # ═══════════════════════════════════════════════════════════════
        _update_progress('extract_pdfs')
        logger.info("[RAG-Only] Building synthetic transcript from stored documents...")

        key_queries = [
            "company overview business model revenue segments",
            "financial performance revenue profit growth",
            "management guidance outlook strategy",
            "risks challenges headwinds concerns",
            "competitive advantage moat market position",
            "quarterly results recent performance",
            "capital allocation dividends buyback capex",
            "industry trends market opportunity",
        ]

        transcript_parts = []
        seen_ids = set()
        for q in key_queries:
            results = rag_query(ticker, q, top_k=5)
            for r in results:
                chunk_hash = hashlib.md5(r['text'].encode()).hexdigest()
                if chunk_hash not in seen_ids:
                    seen_ids.add(chunk_hash)
                    meta = r.get('metadata', {})
                    transcript_parts.append(
                        f"[Source: {meta.get('filename', '?')} | {meta.get('doc_type', '?')}]\n"
                        f"{r['text']}"
                    )

        combined_text = "\n\n---\n\n".join(transcript_parts)
        logger.info(f"[RAG-Only] Built transcript: {len(combined_text)} chars from {len(transcript_parts)} chunks")

        if not combined_text:
            raise ValueError("Could not build transcript from RAG store — documents may be empty.")

        # ═══════════════════════════════════════════════════════════════
        # FETCH FINANCIAL DATA
        # ═══════════════════════════════════════════════════════════════
        _update_progress('fetch_financials')
        fetcher = get_structured_data_fetcher()
        structured_data = fetcher.fetch(ticker)
        financial_tables = structured_data.get("tables", {})

        # ═══════════════════════════════════════════════════════════════
        # V3 ORCHESTRATION
        # ═══════════════════════════════════════════════════════════════
        def progress_cb(stage, active, completed, **kwargs):
            extra = {
                "active_agents": active,
                "completed_agents": completed
            }
            if "agent_outputs" in kwargs:
                extra["agent_outputs"] = kwargs["agent_outputs"]
            _update_progress(stage, extra)

        user_query = f"Analyze {ticker} focusing on forensics, competitive moat, narrative shifts, and capital allocation."
        
        # Auto-detected sector from Screener.in (e.g., "Fast Moving Consumer Goods")
        detected_sector = structured_data.get("sector", "General")
        logger.info(f"[Pipeline] Starting V3 Orchestrator for {ticker} | Sector: {detected_sector}")
        state = _runner.run(analyze(
            ticker=ticker,
            document_text=combined_text,
            financial_tables=financial_tables,
            sector=detected_sector,
            query=user_query,
            progress_callback=progress_cb
        ))

        # ═══════════════════════════════════════════════════════════════
        # ASSEMBLE FINAL REPORT
        # ═══════════════════════════════════════════════════════════════
        _update_progress('assemble')
        logger.info("[Pipeline] Assembling final Novus report...")

        agent_outputs, triage_result, forensic_scorecard, evasion_data, agent_trails_summary = build_ui_payloads(state)

        _update_progress('assemble', {
            "final_report": state.final_report,
            "agent_outputs": agent_outputs,
            "triage_result": triage_result,
            "forensic_scorecard": forensic_scorecard,
            "evasion_data": evasion_data,
            "agent_trails": agent_trails_summary,
            "active_agents": [],
            "completed_agents": ["planning", "forensic_quant", "forensic_investigator", "narrative_decoder", "moat_architect", "capital_allocator", "management_quality", "synthesis"]
        })

        return {
            "final_report": state.final_report,
            "agent_outputs": agent_outputs,
            "triage_result": triage_result,
            "forensic_scorecard": forensic_scorecard,
            "evasion_data": evasion_data,
            "agent_trails": agent_trails_summary,
            "signal_payload": state.signal_payload.model_dump() if getattr(state, "signal_payload", None) else None,
            "status": "completed"
        }

    except Exception as e:
        _update_progress('failed', {"error": str(e)})
        raise


def generate_financial_report(ticker, files_data):
    """
    Background task: Full Novus FinLLM Multi-Agent Report Generation.
    """
    try:
        # ═══════════════════════════════════════════════════════════════
        # AGENT 2: EXTRACTION PIPELINE (runs first — needs PDFs)
        # ═══════════════════════════════════════════════════════════════
        _update_progress('extract_pdfs')
        logger.info(f"[Agent 2: Extraction] Processing PDFs...")

        extraction_result = run_extraction_pipeline(ticker, files_data)
        combined_text = extraction_result.raw_text

        if not combined_text:
            raise ValueError("Could not extract text from the provided PDF files. This usually happens if the PDF is a scanned image without an OCR (text) layer.")

        # ═══════════════════════════════════════════════════════════════
        # RAG INGESTION
        # ═══════════════════════════════════════════════════════════════
        _update_progress('ingest_rag')
        logger.info("[RAG Engine] Ingesting documents into vector store...")

        try:
            rag_files = [(f"doc_{i+1}.pdf", fb) for i, fb in enumerate(files_data)]
            rag_stats = ingest_documents(ticker, rag_files)
            logger.info(f"[RAG Engine] ✅ Ingested {rag_stats['total_chunks']} chunks, types={rag_stats['doc_types']}")
        except Exception as e:
            logger.info(f"[RAG Engine] ⚠️ RAG ingestion failed (non-fatal): {e}")
            rag_stats = {"total_chunks": 0, "doc_types": [], "error": str(e)}

        # ═══════════════════════════════════════════════════════════════
        # FETCH FINANCIAL DATA
        # ═══════════════════════════════════════════════════════════════
        _update_progress('fetch_financials')
        fetcher = get_structured_data_fetcher()
        structured_data = fetcher.fetch(ticker)
        financial_tables = structured_data.get("tables", {})

        # ═══════════════════════════════════════════════════════════════
        # V3 ORCHESTRATION
        # ═══════════════════════════════════════════════════════════════
        def progress_cb(stage, active, completed, **kwargs):
            extra = {
                "active_agents": active,
                "completed_agents": completed
            }
            if "agent_outputs" in kwargs:
                extra["agent_outputs"] = kwargs["agent_outputs"]
            _update_progress(stage, extra)

        user_query = f"Analyze {ticker} focusing on forensics, competitive moat, narrative shifts, and capital allocation."
        
        logger.info(f"[Pipeline] Starting V3 Orchestrator for {ticker}")
        state = _runner.run(analyze(
            ticker=ticker,
            document_text=combined_text,
            financial_tables=financial_tables,
            sector="General / Diversified",
            query=user_query,
            progress_callback=progress_cb
        ))

        # ═══════════════════════════════════════════════════════════════
        # ASSEMBLE FINAL REPORT
        # ═══════════════════════════════════════════════════════════════
        _update_progress('assemble')
        logger.info("[Pipeline] Assembling final Novus report...")

        agent_outputs, triage_result, forensic_scorecard, evasion_data, agent_trails_summary = build_ui_payloads(state)

        _update_progress('assemble', {
            "final_report": state.final_report,
            "agent_outputs": agent_outputs,
            "triage_result": triage_result,
            "forensic_scorecard": forensic_scorecard,
            "evasion_data": evasion_data,
            "agent_trails": agent_trails_summary,
            "active_agents": [],
            "completed_agents": ["planning", "forensic_quant", "forensic_investigator", "narrative_decoder", "moat_architect", "capital_allocator", "management_quality", "synthesis"]
        })

        report_data = {
            "final_report": state.final_report,
            "agent_outputs": agent_outputs,
            "triage_result": triage_result,
            "forensic_scorecard": forensic_scorecard,
            "evasion_data": evasion_data,
            "agent_trails": agent_trails_summary,
            "rag_stats": rag_stats,
            "signal_payload": state.signal_payload.model_dump() if getattr(state, "signal_payload", None) else None,
            "status": "completed",
        }

        logger.info(f"[Pipeline] ✅ Report generation complete for {ticker}")
        return report_data

    except Exception as e:
        _update_progress('failed', {"error": str(e)})
        raise