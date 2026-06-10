"""Tests for the agent confidence scorer (core/agent_base_v3._compute_confidence).

Covers the fixes for chronically low radar scores:
  1. Evidence counter accepts citation-key synonyms (quote/citation/basis/…).
  2. Critic critical-error penalty is capped (no double-punishing).
  3. REQUIRED_INPUTS makes the completeness term real, including the
     "document_text" special requirement for NLP agents.
"""

import pytest

from core.react_engine import ReActResult
from agents.narrative_decoder import NarrativeDecoderV3
from agents.management_quality import ManagementQualityV3
from agents.capital_allocator import CapitalAllocatorV3


LONG_DOC = "Earnings call transcript. " * 50  # > 500 chars


def _confidence(agent, findings, verif=None, doc_text=LONG_DOC, tables=None):
    react = ReActResult(final_output=findings)
    return agent._compute_confidence(react, verif, doc_text, tables or {})


# ── 1. Evidence-key synonyms ─────────────────────────────────────────────────

def test_evidence_synonyms_count_as_grounded():
    agent = NarrativeDecoderV3()
    findings = {
        "tone_shifts": [
            {"topic": "Rural demand", "source_citation": "[Q4 Call]"},
            {"topic": "Margins", "quote": "We expect margin recovery"},
            {"topic": "Capex", "citation": "[AR FY25 p.12]"},
            {"topic": "Guidance", "evidence_prior": "Q3 transcript p.4"},
        ],
    }
    conf, reasons = _confidence(agent, findings)
    assert not any("Missing evidence" in r for r in reasons)
    assert conf == 1.0


def test_ungrounded_findings_still_penalized():
    agent = NarrativeDecoderV3()
    findings = {
        "tone_shifts": [
            {"topic": "Rural demand", "significance": "HIGH"},  # no citation key
            {"topic": "Margins", "quote": "We expect margin recovery"},
        ],
    }
    conf, reasons = _confidence(agent, findings)
    assert any("Missing evidence for 1 finding" in r for r in reasons)
    assert conf < 1.0


# ── 2. Capped critic penalty ─────────────────────────────────────────────────

def test_critical_error_penalty_is_capped():
    agent = NarrativeDecoderV3()
    findings = {"summary": {"text": "x", "evidence": "[doc]"}}
    verif = {"critical_errors": [f"err {i}" for i in range(6)]}
    conf_many, _ = _confidence(agent, findings, verif=verif)
    verif_two = {"critical_errors": ["err 0", "err 1"]}
    conf_two, _ = _confidence(agent, findings, verif=verif_two)
    # 6 errors must not punish more than the 0.2 cap (same as 2 errors).
    assert conf_many == conf_two == pytest.approx(0.8)


# ── 3. REQUIRED_INPUTS completeness ──────────────────────────────────────────

def test_document_text_requirement_fails_on_empty_corpus():
    agent = NarrativeDecoderV3()
    findings = {"summary": {"text": "x", "evidence": "[doc]"}}
    conf_thin, reasons = _confidence(agent, findings, doc_text="too short")
    assert any("document corpus" in r for r in reasons)
    conf_full, _ = _confidence(agent, findings, doc_text=LONG_DOC)
    assert conf_full > conf_thin


def test_quant_agent_requires_financial_tables():
    agent = CapitalAllocatorV3()
    findings = {"summary": {"text": "x", "evidence": "[doc]"}}
    full_tables = {
        "profit_loss": {"Mar 2025": {"Sales": 1.0}},
        "balance_sheet": {"Mar 2025": {"Equity": 1.0}},
        "cash_flow": {"Mar 2025": {"OCF": 1.0}},
    }
    conf_full, reasons_full = _confidence(agent, findings, doc_text="", tables=full_tables)
    assert conf_full == 1.0
    assert not reasons_full

    conf_missing, reasons_missing = _confidence(agent, findings, doc_text="", tables={})
    assert conf_missing < conf_full
    assert any("Missing required input" in r for r in reasons_missing)


def test_management_quality_requires_shareholding_table():
    agent = ManagementQualityV3()
    findings = {"summary": {"text": "x", "evidence": "[doc]"}}
    with_table = {"shareholding": {"Mar 2026": {"Promoters": 46.89}}}
    conf_with, _ = _confidence(agent, findings, tables=with_table)
    conf_without, reasons = _confidence(agent, findings, tables={})
    assert conf_with > conf_without
    assert any("shareholding" in r for r in reasons)


# ── Shareholding tool ────────────────────────────────────────────────────────

def test_shareholding_tool_returns_table():
    agent = ManagementQualityV3()
    tables = {"shareholding": {"Mar 2026": {"Promoters": 46.89, "FIIs": 20.5}}}
    tools = {t.name: t for t in agent.build_agent_tools("doc", tables, "LUPIN")}
    assert "get_shareholding_pattern" in tools
    out = tools["get_shareholding_pattern"].handler(scope="full")
    assert out["shareholding_pattern_pct"]["Mar 2026"]["Promoters"] == 46.89


def test_shareholding_tool_missing_table_is_explicit():
    agent = ManagementQualityV3()
    tools = {t.name: t for t in agent.build_agent_tools("doc", {}, "LUPIN")}
    out = tools["get_shareholding_pattern"].handler()
    assert out["shareholding_pattern"] == "DATA NOT AVAILABLE"
