"""Tests for deterministic re-runs: report cache, framework cache,
mistake dedupe, and canonical agent ordering. No network required."""

import os

import pytest

from core.memory import MemoryLayer


@pytest.fixture()
def mem(tmp_path):
    return MemoryLayer(db_path=str(tmp_path / "test_memory.db"))


class TestReportCache:
    def test_miss_returns_none(self, mem):
        assert mem.get_cached_report("CIPLA", "FY26", "rag") is None

    def test_store_and_lookup(self, mem):
        payload = {"final_report": "## Report", "recommendation": "ADD", "status": "completed"}
        mem.store_report("CIPLA", "FY26", "rag", payload)

        cached = mem.get_cached_report("CIPLA", "FY26", "rag")
        assert cached is not None
        assert cached["final_report"] == "## Report"
        assert cached["recommendation"] == "ADD"
        assert "cached_at" in cached

    def test_upsert_overwrites(self, mem):
        mem.store_report("CIPLA", "FY26", "rag", {"final_report": "v1"})
        mem.store_report("CIPLA", "FY26", "rag", {"final_report": "v2"})

        cached = mem.get_cached_report("CIPLA", "FY26", "rag")
        assert cached["final_report"] == "v2"

    def test_keyed_by_ticker_and_period(self, mem):
        mem.store_report("CIPLA", "FY26", "rag", {"final_report": "cipla-fy26"})
        assert mem.get_cached_report("LUPIN", "FY26", "rag") is None
        assert mem.get_cached_report("CIPLA", "FY25", "rag") is None
        assert mem.get_cached_report("cipla", "FY26", "rag")["final_report"] == "cipla-fy26"


class TestFrameworkCache:
    def test_roundtrip(self, mem):
        frameworks = {"forensic_investigator": "focus A", "moat_architect": "focus B"}
        mem.store_frameworks("LUPIN", "Q4_FY26", "abc123", frameworks)
        assert mem.get_cached_frameworks("LUPIN", "Q4_FY26", "abc123") == frameworks

    def test_profile_hash_isolates(self, mem):
        mem.store_frameworks("LUPIN", "Q4_FY26", "hash-a", {"x": "1"})
        assert mem.get_cached_frameworks("LUPIN", "Q4_FY26", "hash-b") is None


class TestMistakeDedupe:
    CORRECTION = {
        "agent_name": "forensic_quant",
        "original_claim": "ROIC was 25% in FY26",
        "verified_fact": "ROIC was 18.2% in FY26",
        "metric_category": "roic",
        "confidence": 0.9,
    }

    def test_rerun_does_not_duplicate(self, mem):
        findings = {"corrections": [dict(self.CORRECTION)], "unverifiable_claims": []}

        first = mem.store_corrections(findings, "LUPIN", "Q4_FY26")
        assert first["mistakes_written"] == 1

        second = mem.store_corrections(findings, "LUPIN", "Q4_FY26")
        assert second["mistakes_written"] == 0
        assert second.get("duplicates_skipped") == 1

        with mem._connect() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM agent_mistakes").fetchone()["n"]
        assert n == 1

    def test_unverifiable_rerun_does_not_bump_gap_count(self, mem):
        findings = {
            "corrections": [],
            "unverifiable_claims": [{
                "agent_name": "moat_architect",
                "claim": "Market share is 30%",
                "reason": "No source found",
                "metric_category": "competitive_position",
            }],
        }
        mem.store_corrections(findings, "LUPIN", "Q4_FY26")
        mem.store_corrections(findings, "LUPIN", "Q4_FY26")

        with mem._connect() as conn:
            row = conn.execute(
                "SELECT occurrence_count FROM data_gaps WHERE ticker='LUPIN'"
            ).fetchone()
        assert row["occurrence_count"] == 1

    def test_different_period_is_not_a_duplicate(self, mem):
        findings = {"corrections": [dict(self.CORRECTION)], "unverifiable_claims": []}
        mem.store_corrections(findings, "LUPIN", "Q3_FY26")
        second = mem.store_corrections(findings, "LUPIN", "Q4_FY26")
        # The correction dict carries no fiscal_period, so the run-level
        # period applies — a new period is a genuinely new observation.
        assert second["mistakes_written"] == 1


class TestCanonicalAgentOrder:
    def test_build_ui_payloads_orders_agents(self):
        from tasks import build_ui_payloads, AGENT_ORDER
        from cio_orchestrator import OrchestratorState
        from core.agent_base_v3 import AuditTrail

        state = OrchestratorState(ticker="LUPIN", sector="Pharma", query="q")
        # Insert in a scrambled "completion" order
        scrambled = [
            "management_quality", "forensic_quant", "capital_allocator",
            "narrative_decoder", "pm_synthesis", "forensic_investigator",
            "moat_architect", "critic_agent",
        ]
        for name in scrambled:
            trail = AuditTrail(agent_name=name, ticker="LUPIN")
            trail.findings = {"note": name}
            trail.confidence = 0.5
            state.agent_trails[name] = trail

        a_outs, _, _, _, trails_summary = build_ui_payloads(state)

        assert list(a_outs.keys()) == AGENT_ORDER
        assert list(trails_summary.keys()) == AGENT_ORDER

    def test_unknown_agents_appended_last(self):
        from tasks import build_ui_payloads
        from cio_orchestrator import OrchestratorState
        from core.agent_base_v3 import AuditTrail

        state = OrchestratorState(ticker="LUPIN", sector="Pharma", query="q")
        for name in ["mystery_agent", "forensic_quant"]:
            trail = AuditTrail(agent_name=name, ticker="LUPIN")
            trail.findings = {"note": name}
            state.agent_trails[name] = trail

        _, _, _, _, trails_summary = build_ui_payloads(state)
        keys = list(trails_summary.keys())
        assert keys[0] == "forensic_quant"
        assert keys[-1] == "mystery_agent"
