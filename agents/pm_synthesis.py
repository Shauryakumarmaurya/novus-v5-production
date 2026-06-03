import json
from core.agent_base_v3 import AgentV3

class PMSynthesisV3(AgentV3):
    MAX_ITERATIONS = 1   
    VERIFY = False        

    @property
    def agent_name(self) -> str:
        return "pm_synthesis"

    @property
    def agent_role(self) -> str:
        return (
            "You are an elite Portfolio Manager at a top-tier institutional fund focused on generating ALPHA. "
            "Your job is NOT to just summarize what the junior analysts (specialist agents) have found. "
            "Your job is to be an 'unmatched synthesizer' — you must cross-reference their findings to identify "
            "market-moving information, hidden correlations, and micro-trends that human analysts physically cannot process in time. "
            "Look for contradictions between agents (e.g., strong narrative but weak forensic cash flow). "
            "Identify the 'Variant Perception' — what is the market currently pricing in, and why is the market wrong based on our data? "
            "Your thesis must be a high-conviction, revenue-generating insight: BUY, WATCH, or PASS with measurable kill criteria.\n\n"
            "MANDATORY DATA GAP PROTOCOL:\n"
            "If ANY upstream agent reported data_gaps in their findings, you MUST:\n"
            "1. State: '[METRIC] could not be calculated due to missing [FIELD].'\n"
            "2. DO NOT infer, estimate, or narrativize around the missing value.\n"
            "3. DO NOT say 'data suggests' or 'likely' for any metric you haven't seen.\n"
            "4. If a ratio like Other Income/PBT has status DATA_NOT_AVAILABLE, acknowledge it as "
            "'unverifiable' — do NOT substitute an estimate.\n"
            "Violation of this protocol produces a misleading research note that could "
            "cause real capital losses."
        )

    @property
    def output_example(self) -> str:
        return """{
  "executive_summary": "High-conviction 1-paragraph summary of the investment case and the core variant perception.",
  "alpha_synthesis": "Deep cross-referencing of agent findings. E.g., 'While Moat Architect sees strong pricing power, Forensic Quant flags deteriorating cash conversion, and Narrative Decoder caught management dodging margin questions — indicating the moat is actually breaking.'",
  "variant_perception": "What the market is NOT pricing in — your edge. Why is the consensus wrong?",
  "fundamental_analysis": "Deep paragraph on business model, moat, and competitive position.",
  "forensic_audit": "Deep paragraph on accounting quality, earnings quality, and red flags.",
  "capital_allocation": "Deep paragraph on management's capital stewardship, M&A, and returns policy.",
  "management_quality": "Deep paragraph on governance, promoter integrity, and KMP stability.",
  "bull_case": ["Pillar 1 with evidence", "Pillar 2 with evidence", "Pillar 3"],
  "bear_case": [
    {"risk": "Description with evidence", "probability": "LOW|MEDIUM|HIGH", "impact": "Description"}
  ],
  "scoreboard": {
    "forensic_quality": "A|B|C|D",
    "management_score": "A|B|C|D",
    "moat_durability": "STRONG|INTACT|WEAKENING|BROKEN",
    "pricing_verdict": "CHEAP|FAIR|EXPENSIVE",
    "reverse_dcf_implied_growth": null
  },
  "recommendation": "BUY|WATCH|PASS",
  "kill_criteria": [
    "ROIC drops below 12% for 2 consecutive quarters",
    "Promoter pledge exceeds 10% of holding"
  ],
  "open_questions_for_management": [
    "Could you provide a bridge for the 40% jump in other income?",
    "Why have CWIP projects extended past the 24-month delay range?"
  ]
}"""

    def build_initial_context(self, ticker, sector, signals, doc_chars) -> str:
        agent_outputs = signals.get("_agent_outputs", {})
        parts = [
            f"Synthesise findings for {ticker} ({sector}).",
            "CRITICAL: Do not just summarize. Cross-reference the agent findings. Look for contradictions, hidden correlations, and identify the Variant Perception (Alpha)."
        ]
        for agent_name, output in agent_outputs.items():
            parts.append(f"\n## {agent_name.upper()} FINDINGS:")
            if isinstance(output, dict):
                parts.append(json.dumps(output, indent=2, ensure_ascii=False))
            else:
                parts.append(str(output)[:5000])
        return "\n".join(parts)
