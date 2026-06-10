"""Tests for the data-integrity hardening roadmap:

P0 — Screener snapshot fallback + fail-loud on empty tables
P1 — risk-sweep extraction signals (audit red flags, pledges)
P2 — guidance track record persistence
P4 — thesis ledger record/evaluate roundtrip
P5 — calendar-year filer fiscal labeling + CY retrieval windows

No network required.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from core.memory import MemoryLayer


@pytest.fixture()
def mem(tmp_path):
    return MemoryLayer(db_path=str(tmp_path / "test_memory.db"))


# ═══════════════════════════════════════════════════════════════════════════
# P0 — Screener snapshot fallback
# ═══════════════════════════════════════════════════════════════════════════

class TestScreenerSnapshots:
    PAYLOAD = {
        "ticker": "LUPIN",
        "sector": "Pharmaceuticals",
        "tables": {"profit_loss": {"Mar 2025": {"Sales": 20000.0}}},
    }

    def test_roundtrip(self, mem):
        mem.store_screener_snapshot("LUPIN", self.PAYLOAD)
        snap = mem.get_screener_snapshot("LUPIN")
        assert snap is not None
        assert snap["tables"]["profit_loss"]["Mar 2025"]["Sales"] == 20000.0
        assert "snapshot_at" in snap

    def test_empty_payload_not_stored(self, mem):
        mem.store_screener_snapshot("LUPIN", {"ticker": "LUPIN", "tables": {}})
        assert mem.get_screener_snapshot("LUPIN") is None

    def test_upsert_keeps_latest(self, mem):
        mem.store_screener_snapshot("LUPIN", self.PAYLOAD)
        newer = dict(self.PAYLOAD, sector="Healthcare")
        mem.store_screener_snapshot("LUPIN", newer)
        assert mem.get_screener_snapshot("LUPIN")["sector"] == "Healthcare"


class TestFetcherFallback:
    def _fresh_fetcher(self):
        from structured_data_fetcher import StructuredDataFetcher
        return StructuredDataFetcher()

    def test_live_failure_serves_snapshot(self, mem):
        fetcher = self._fresh_fetcher()
        snapshot = {
            "ticker": "LUPIN", "sector": "Pharma",
            "tables": {"profit_loss": {"Mar 2025": {"Sales": 1.0}}},
            "snapshot_at": "2026-06-01T00:00:00",
        }
        with patch("structured_data_fetcher.fetch_screener_tables", side_effect=ConnectionError("blocked")), \
             patch.object(fetcher, "_load_snapshot", return_value=dict(snapshot)):
            result = fetcher.fetch("LUPIN")
        assert result["from_snapshot"] is True
        assert result["tables"]["profit_loss"]
        assert "snapshot" in result["error"].lower() or "Live fetch failed" in result["error"]

    def test_live_failure_no_snapshot_returns_empty(self, mem):
        fetcher = self._fresh_fetcher()
        with patch("structured_data_fetcher.fetch_screener_tables", side_effect=ConnectionError("blocked")), \
             patch.object(fetcher, "_load_snapshot", return_value=None):
            result = fetcher.fetch("NOSNAP")
        assert result["tables"] == {}
        assert result.get("error")

    def test_successful_fetch_persists_snapshot(self, mem):
        fetcher = self._fresh_fetcher()
        raw = {
            "ticker": "LUPIN", "source": "screener.in", "sector": "Pharma",
            "tables": {"Profit & Loss": [{"Description": "Sales", "Mar 2025": "20,000"}]},
        }
        with patch("structured_data_fetcher.fetch_screener_tables", return_value=raw), \
             patch.object(fetcher, "_persist_snapshot") as persist:
            result = fetcher.fetch("LUPIN")
        assert result["tables"]
        persist.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# P1 — risk sweep extraction signals
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractionSignals:
    def test_audit_red_flags_detected(self):
        from agents.extraction import build_extraction_signals
        text = (
            "The auditors have included an Emphasis of Matter paragraph regarding "
            "the material uncertainty related to going concern. Separately, the "
            "resignation of the statutory auditor was accepted by the Board."
        )
        signals = build_extraction_signals(text)
        assert signals["has_audit_red_flags"] is True
        assert signals["auditor_changed"] is True

    def test_pledge_detected(self):
        from agents.extraction import build_extraction_signals
        text = "As of March 2025, 41% of promoter shares pledged with lenders against borrowing."
        signals = build_extraction_signals(text)
        assert signals["promoter_shares_pledged"] is True

    def test_clean_text_no_signals(self):
        from agents.extraction import build_extraction_signals
        text = "Revenue grew 12% on strong demand. The board declared a dividend."
        signals = build_extraction_signals(text)
        assert signals["has_audit_red_flags"] is False
        assert signals["auditor_changed"] is False
        assert signals["promoter_shares_pledged"] is False
        assert signals["has_rpt_disclosures"] is False

    def test_rpt_and_contingent(self):
        from agents.extraction import build_extraction_signals
        text = (
            "Note 34: Related party transactions with subsidiaries amounted to Rs 450 cr. "
            "Contingent liabilities not provided for: disputed tax demands of Rs 1,200 cr."
        )
        signals = build_extraction_signals(text)
        assert signals["has_rpt_disclosures"] is True
        assert signals["has_contingent_liabilities"] is True

    def test_empty_text(self):
        from agents.extraction import build_extraction_signals
        assert build_extraction_signals("") == {}


# ═══════════════════════════════════════════════════════════════════════════
# P2 — guidance track record
# ═══════════════════════════════════════════════════════════════════════════

class TestGuidanceTrack:
    RECORDS = [
        {"topic": "Margin recovery", "prior_guidance": "18% EBITDA exit", "actual_outcome": "15.2%", "credibility": "LOW"},
        {"topic": "US launches", "prior_guidance": "15 launches", "actual_outcome": "14 launches", "credibility": "HIGH"},
    ]

    def test_store_and_get(self, mem):
        mem.store_guidance_records("LUPIN", "Q3_FY26", self.RECORDS)
        rows = mem.get_guidance_track_record("LUPIN")
        assert len(rows) == 2
        topics = {r["topic"] for r in rows}
        assert topics == {"Margin recovery", "US launches"}

    def test_upsert_no_duplicates(self, mem):
        mem.store_guidance_records("LUPIN", "Q3_FY26", self.RECORDS)
        mem.store_guidance_records("LUPIN", "Q3_FY26", self.RECORDS)
        assert len(mem.get_guidance_track_record("LUPIN")) == 2

    def test_skips_blank_topics(self, mem):
        mem.store_guidance_records("LUPIN", "Q3_FY26", [{"topic": "", "credibility": "LOW"}, "not-a-dict"])
        assert mem.get_guidance_track_record("LUPIN") == []

    def test_digest_excludes_current_period(self, mem):
        mem.store_guidance_records("LUPIN", "Q3_FY26", self.RECORDS)
        with patch("cio_orchestrator.get_memory", return_value=mem):
            from cio_orchestrator import _build_guidance_digest
            assert _build_guidance_digest("LUPIN", "Q3_FY26") == ""
            digest = _build_guidance_digest("LUPIN", "Q4_FY26")
        assert "Margin recovery" in digest
        assert "GUIDANCE TRACK RECORD" in digest

    def test_digest_discount_rule_fires_on_repeated_misses(self, mem):
        records = [
            {"topic": f"Topic {i}", "prior_guidance": "x", "actual_outcome": "y", "credibility": "LOW"}
            for i in range(3)
        ]
        mem.store_guidance_records("LUPIN", "Q2_FY26", records)
        with patch("cio_orchestrator.get_memory", return_value=mem):
            from cio_orchestrator import _build_guidance_digest
            digest = _build_guidance_digest("LUPIN", "Q4_FY26")
        assert "DISCOUNT" in digest


# ═══════════════════════════════════════════════════════════════════════════
# P4 — thesis ledger
# ═══════════════════════════════════════════════════════════════════════════

class TestThesisLedger:
    def test_record_and_pending(self, mem):
        mem.record_thesis("LUPIN", "Q4_FY26", "rag", "ADD", "LIKELY_CONTINUE", 2050.0)
        # Just-published thesis isn't due for any horizon yet.
        assert mem.get_pending_thesis_evaluations(90) == []

    def test_pending_after_horizon(self, mem):
        mem.record_thesis("LUPIN", "Q4_FY26", "rag", "ADD", "LIKELY_CONTINUE", 2050.0)
        old = (datetime.utcnow() - timedelta(days=120)).isoformat()
        with mem._connect() as conn:
            conn.execute("UPDATE thesis_ledger SET published_at=?", (old,))
        pending = mem.get_pending_thesis_evaluations(90)
        assert len(pending) == 1
        assert pending[0]["recommendation"] == "ADD"
        # 180-day horizon not reached yet
        assert mem.get_pending_thesis_evaluations(180) == []

    def test_evaluation_roundtrip(self, mem):
        mem.record_thesis("LUPIN", "Q4_FY26", "rag", "ADD", "LIKELY_CONTINUE", 2000.0)
        old = (datetime.utcnow() - timedelta(days=100)).isoformat()
        with mem._connect() as conn:
            conn.execute("UPDATE thesis_ledger SET published_at=?", (old,))
        row = mem.get_pending_thesis_evaluations(90)[0]
        mem.store_thesis_evaluation(row["id"], 90, {"move_pct": 12.5, "hit": True})

        # Scored → no longer pending
        assert mem.get_pending_thesis_evaluations(90) == []
        track = mem.get_thesis_track_record("LUPIN")
        assert track[0]["eval_t90"]["hit"] is True

    def test_rerun_resets_evaluations(self, mem):
        mem.record_thesis("LUPIN", "Q4_FY26", "rag", "ADD", "LIKELY_CONTINUE", 2000.0)
        old = (datetime.utcnow() - timedelta(days=100)).isoformat()
        with mem._connect() as conn:
            conn.execute("UPDATE thesis_ledger SET published_at=?", (old,))
        row = mem.get_pending_thesis_evaluations(90)[0]
        mem.store_thesis_evaluation(row["id"], 90, {"hit": True})
        # Force-refresh re-publishes the verdict → evals reset
        mem.record_thesis("LUPIN", "Q4_FY26", "rag", "HOLD", "UNCLEAR", 2100.0)
        track = mem.get_thesis_track_record("LUPIN")
        assert track[0]["recommendation"] == "HOLD"
        assert "eval_t90" not in track[0]

    def test_hit_logic(self):
        from scripts.scheduled_refresh import evaluate_thesis_ledger  # import check only
        assert callable(evaluate_thesis_ledger)


# ═══════════════════════════════════════════════════════════════════════════
# P5 — calendar-year filers
# ═══════════════════════════════════════════════════════════════════════════

class TestCalendarYearFilers:
    DEC_TABLES = {
        "profit_loss": {"Dec 2023": {"Sales": 1.0}, "Dec 2024": {"Sales": 2.0}, "Dec 2025": {"Sales": 3.0}},
        "balance_sheet": {"Dec 2024": {"Equity": 1.0}, "Dec 2025": {"Equity": 2.0}},
    }
    MAR_TABLES = {
        "profit_loss": {"Mar 2024": {"Sales": 1.0}, "Mar 2025": {"Sales": 2.0}},
        "quarterly_results": {"Dec 2025": {"Sales": 0.5}},
    }

    def test_dec_filer_annual_label(self):
        from cio_orchestrator import _infer_fiscal_period
        assert _infer_fiscal_period(self.DEC_TABLES) == "CY25"

    def test_dec_filer_quarterly_label(self):
        from cio_orchestrator import _infer_fiscal_period
        tables = dict(self.DEC_TABLES, quarterly_results={"Mar 2026": {"Sales": 1.0}})
        assert _infer_fiscal_period(tables) == "Q1_CY26"

    def test_march_filer_unchanged(self):
        from cio_orchestrator import _infer_fiscal_period
        # Dec quarterly column on a Mar-ending filer stays Indian FY (Q3)
        assert _infer_fiscal_period(self.MAR_TABLES) == "Q3_FY26"

    def test_cy_window_maps_to_overlapping_fys(self):
        from core.tools import fiscal_year_window
        window = fiscal_year_window("Q4_CY25")
        assert window == ["FY26", "FY25", "FY24"]

    def test_fy_window_unchanged(self):
        from core.tools import fiscal_year_window
        assert fiscal_year_window("Q3_FY26") == ["FY26", "FY25"]

    def test_unknown_label_returns_none(self):
        from core.tools import fiscal_year_window
        assert fiscal_year_window("garbage") is None


# ═══════════════════════════════════════════════════════════════════════════
# P3 — peer table normalization
# ═══════════════════════════════════════════════════════════════════════════

class TestPeerNormalization:
    RAW = [
        {"S.No.": "1", "Name": "Sun Pharma", "CMP  Rs.": "1,650.5", "P/E": "35.2", "ROCE  %": "18.9"},
        {"S.No.": "2", "Name": "Lupin", "CMP  Rs.": "2,050.0", "P/E": "28.4", "ROCE  %": "21.3"},
        {"S.No.": "", "Name": "Median: 8 Co.", "CMP  Rs.": "1,100.0", "P/E": "30.1", "ROCE  %": "17.2"},
        {"S.No.": "x", "Name": ""},  # dropped: no name
    ]

    def test_rows_normalized(self):
        from structured_data_fetcher import _clean_peer_rows
        rows = _clean_peer_rows(self.RAW)
        assert len(rows) == 3
        assert rows[0]["Name"] == "Sun Pharma"
        assert rows[0]["P/E"] == 35.2
        assert "S.No." not in rows[0]

    def test_median_row_preserved(self):
        from structured_data_fetcher import _clean_peer_rows
        rows = _clean_peer_rows(self.RAW)
        assert any("Median" in r["Name"] for r in rows)

    def test_normalize_tables_routes_peers(self):
        from structured_data_fetcher import _normalize_tables
        out = _normalize_tables({"Peer Comparison": self.RAW})
        assert isinstance(out["peers"], list)
        assert out["peers"][0]["Name"] == "Sun Pharma"

    def test_critic_tool_with_and_without_peers(self):
        from agents.critic_agent import CriticAgentV3
        critic = CriticAgentV3()
        tools = critic.build_agent_tools("", {"peers": [{"Name": "X", "P/E": 10.0}]}, ticker="LUPIN")
        assert tools[0].name == "compare_to_peers"
        result = tools[0].handler()
        assert result["peer_comparison"][0]["Name"] == "X"

        tools_empty = critic.build_agent_tools("", {}, ticker="LUPIN")
        result_empty = tools_empty[0].handler()
        assert result_empty["peer_comparison"] == "DATA NOT AVAILABLE"
