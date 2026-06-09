"""Auditor gate: chronological hallucinations must be rejected pre-LLM."""
from utils.temporal_logic import verify_chronology


def test_verify_chronology_ordering():
    assert verify_chronology("Q1 FY24", "Q3 FY24") is True
    assert verify_chronology("Q3 FY25", "Q1 FY24") is False
    assert verify_chronology("FY23", "FY24") is True
    # Unparseable / N/A dates pass through (cannot verify cleanly)
    assert verify_chronology("N/A", "Q3 FY24") is True
    assert verify_chronology("", "") is True


def test_critic_rejects_timeline_hallucination():
    """A cause dated AFTER its effect must short-circuit to REJECTED status
    without any LLM call (deterministic pre-filter)."""
    from agents.critic_agent import CriticAgentV3

    critic = CriticAgentV3()
    trail = critic.execute(
        ticker="TCS",
        document_text="",
        financial_tables={},
        sector="IT",
        peer_findings={
            "narrative_decoder": {
                "causal_chain": {
                    "cause_date": "Q3 FY26",
                    "effect_date": "Q1 FY24",
                    "claim": "Late event caused earlier outcome",
                }
            }
        },
    )
    assert trail.findings["verification_status"] == "REJECTED_TIMELINE_HALLUCINATION"
    assert trail.confidence == 0.0
    assert trail.llm_calls == 0


def test_critic_passes_through_empty_findings():
    from agents.critic_agent import CriticAgentV3

    critic = CriticAgentV3()
    trail = critic.execute(
        ticker="TCS",
        document_text="",
        financial_tables={},
        sector="IT",
        peer_findings={},
    )
    assert trail.findings["verification_status"] == "NO_FINDINGS_TO_VERIFY"
