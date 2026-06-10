"""Tests for the critic JSON-truncation fix:

1. _repair_truncated_json / _extract_json salvage output cut by a token cap
2. react_loop threads max_tokens into every llm.call (normal + forced-final)
3. CriticAgentV3 carries the 8192 budget and re-attaches known
   narrative_inconsistencies in Python instead of LLM echo

No network required.
"""

import json
from unittest.mock import patch

import pytest

from core.llm_client import LLMResponse
from core.react_engine import (
    ReActResult,
    _extract_json,
    _repair_truncated_json,
    react_loop,
)
from core.tools import ToolRegistry


# ═══════════════════════════════════════════════════════════════════════════
# JSON salvage
# ═══════════════════════════════════════════════════════════════════════════

FULL = {
    "corrections": [
        {
            "agent_name": "moat_architect",
            "metric_category": "distribution_reach",
            "original_claim": "13M stores",
            "verified_fact": "9M stores",
            "citations": [{"doc_id": "ar_fy24.pdf", "snippet": "approximately 9 million outlets", "page": 42}],
            "confidence": 0.95,
        },
        {
            "agent_name": "capital_allocator",
            "metric_category": "dividend_policy",
            "original_claim": "payout 95% signals weakness",
            "verified_fact": "payout 95% is sector standard",
            "citations": [],
            "confidence": 0.85,
        },
    ],
    "unverifiable_claims": [
        {"agent_name": "narrative_decoder", "claim": "8% volume guidance", "action": "FLAGGED_AS_DATA_GAP"},
    ],
    "verification_status": "CLEARED WITH CORRECTIONS",
}


class TestRepairTruncatedJson:
    def test_truncated_mid_string_recovers_prior_corrections(self):
        full_text = json.dumps(FULL, indent=2)
        # Cut inside the second correction's "verified_fact" string —
        # exactly what a token cap does.
        cut = full_text.find("sector standard")
        truncated = full_text[:cut + 5]

        result = _repair_truncated_json(truncated)
        assert result is not None
        assert result["corrections"][0]["verified_fact"] == "9M stores"

    def test_many_cut_points_never_crash_and_often_recover(self):
        full_text = json.dumps(FULL)
        recovered = 0
        for cut in range(50, len(full_text) - 1, 37):
            result = _repair_truncated_json(full_text[:cut])
            if result is not None:
                assert isinstance(result, dict)
                recovered += 1
        assert recovered > 0

    def test_complete_json_returns_none(self):
        # Structurally complete input means the parse failure was something
        # else — repair must not mask that.
        assert _repair_truncated_json(json.dumps(FULL)) is None

    def test_non_object_input_returns_none(self):
        assert _repair_truncated_json("not json at all") is None
        assert _repair_truncated_json("") is None

    def test_extract_json_salvages_truncated_payload(self):
        full_text = json.dumps(FULL, indent=2)
        truncated = full_text[: full_text.find("FLAGGED") + 4]
        result = _extract_json(truncated)
        assert result is not None
        assert len(result["corrections"]) == 2

    def test_extract_json_still_parses_clean_payload(self):
        assert _extract_json(json.dumps(FULL)) == FULL


# ═══════════════════════════════════════════════════════════════════════════
# max_tokens propagation through react_loop
# ═══════════════════════════════════════════════════════════════════════════

class StubLLM:
    """Records every call's kwargs; immediately returns a final JSON answer."""

    def __init__(self):
        self.calls = []

    def call(self, messages, tools=None, max_tokens=None, **kwargs):
        self.calls.append({"tools": tools, "max_tokens": max_tokens})
        return LLMResponse(content='{"ok": true}', finish_reason="stop")


class TestMaxTokensThreading:
    def test_normal_path_passes_max_tokens(self):
        llm = StubLLM()
        result = react_loop(
            system_prompt="sys", initial_context="ctx",
            tools=ToolRegistry(), max_iterations=5, llm=llm, max_tokens=8192,
        )
        assert result.final_output == {"ok": True}
        assert llm.calls[0]["max_tokens"] == 8192

    def test_forced_final_passes_max_tokens(self):
        # max_iterations=1 goes straight to the forced-final (no tools) call —
        # the path the critic always hits.
        llm = StubLLM()
        result = react_loop(
            system_prompt="sys", initial_context="ctx",
            tools=ToolRegistry(), max_iterations=1, llm=llm, max_tokens=8192,
        )
        assert result.final_output == {"ok": True}
        assert llm.calls[-1]["tools"] is None
        assert llm.calls[-1]["max_tokens"] == 8192

    def test_default_budget_is_client_default(self):
        llm = StubLLM()
        react_loop(
            system_prompt="sys", initial_context="ctx",
            tools=ToolRegistry(), max_iterations=5, llm=llm,
        )
        assert llm.calls[0]["max_tokens"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Critic budget + Python-side inconsistency merge
# ═══════════════════════════════════════════════════════════════════════════

KNOWN_INCONSISTENCIES = [
    {
        "metric_category": "demand_commentary",
        "fiscal_period_a": "Q2_FY26",
        "fiscal_period_b": "Q3_FY26",
        "inconsistency_type": "REASON_DRIFT",
        "severity": "HIGH",
    }
]


class FakeMemory:
    def get_management_inconsistencies(self, ticker):
        return KNOWN_INCONSISTENCIES


class TestCriticAgent:
    def test_budget_is_v3_max(self):
        from agents.critic_agent import CriticAgentV3
        assert CriticAgentV3.MAX_OUTPUT_TOKENS == 8192

    def _run_critic(self, fake_loop):
        from agents.critic_agent import CriticAgentV3
        critic = CriticAgentV3()
        with patch("core.react_engine.react_loop", side_effect=fake_loop), \
             patch("core.memory.get_memory", return_value=FakeMemory()):
            return critic.execute(
                ticker="LUPIN",
                document_text="some document text " * 50,
                financial_tables={},
                sector="Pharma",
                peer_findings={"forensic_quant": {"roic": "18%"}},
            )

    def test_inconsistencies_merged_in_python(self):
        seen = {}

        def fake_loop(**kwargs):
            seen.update(kwargs)
            return ReActResult(final_output={"corrections": [], "verification_status": "CLEARED"})

        trail = self._run_critic(fake_loop)
        # Budget threaded into the loop
        assert seen["max_tokens"] == 8192
        # Known inconsistencies attached deterministically
        assert trail.findings["narrative_inconsistencies"] == KNOWN_INCONSISTENCIES
        # Prompt no longer demands a verbatim echo
        assert "echo them verbatim" not in seen["initial_context"]
        assert "do NOT echo" in seen["initial_context"]

    def test_no_merge_when_findings_empty(self):
        def fake_loop(**kwargs):
            return ReActResult(final_output=None)

        trail = self._run_critic(fake_loop)
        assert trail.findings is None
