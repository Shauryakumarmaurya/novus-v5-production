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
            "Your thesis must be a high-conviction, revenue-generating insight: an explicit ADD, HOLD, or SELL with measurable kill criteria.\n\n"
            "STORYTELLER MANDATE:\n"
            "Your report must read as a coherent narrative an analyst can retell to a client in two minutes: "
            "what happened to this stock and why (the past), what is driving it now (the present), and what must "
            "stay true for the move to continue (the future). When a PRICE ACTION DOSSIER is provided in your mandate, "
            "you MUST populate the 'stock_story' object — explaining each decline and rally episode using only "
            "evidence from the dossier, the specialist findings, and the filings. If no dossier is provided, omit "
            "'stock_story' entirely and list it as a data gap.\n\n"
            "BUCKET DISCIPLINE:\n"
            "'bull_case' is a STRICT positives bucket and 'bear_case' is a STRICT negatives bucket. "
            "Never mix a caveat into a positive or a silver lining into a negative — each item lives in exactly "
            "one bucket and must cite specific evidence (a number, a quote, or a filing fact).\n\n"
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
  "stock_story": {
    "past_declines": [
      {"period": "Oct 2021 - Jun 2022 (FY22-FY23)", "magnitude": "-38%", "causes": "Margin compression from API cost inflation (gross margin -420bps) and US price erosion, per FY22 commentary."}
    ],
    "past_rallies": [
      {"period": "Jun 2022 - Sep 2024 (FY23-FY25)", "magnitude": "+95%", "causes": "US generics launch cycle drove PAT from X to Y Cr; margin guidance raised twice."}
    ],
    "current_driver": "What is driving the CURRENT (ongoing) move, with evidence.",
    "continuation_verdict": "LIKELY|MIXED|UNLIKELY",
    "what_must_stay_true": ["Condition 1 for the move to continue", "Condition 2"]
  },
  "fundamental_analysis": "Deep paragraph on business model, moat, and competitive position.",
  "forensic_audit": "Deep paragraph on accounting quality, earnings quality, and red flags.",
  "capital_allocation": "Deep paragraph on management's capital stewardship, M&A, and returns policy.",
  "management_quality": "Deep paragraph on governance, promoter integrity, and KMP stability.",
  "bull_case": ["STRICT positives bucket. Pillar 1 with evidence", "Pillar 2 with evidence", "Pillar 3"],
  "bear_case": [
    {"risk": "STRICT negatives bucket. Description with evidence", "probability": "LOW|MEDIUM|HIGH", "impact": "Description"}
  ],
  "scoreboard": {
    "forensic_quality": "A|B|C|D",
    "management_score": "A|B|C|D",
    "moat_durability": "STRONG|INTACT|WEAKENING|BROKEN",
    "pricing_verdict": "CHEAP|FAIR|EXPENSIVE",
    "reverse_dcf_implied_growth": null
  },
  "recommendation": "ADD|HOLD|SELL",
  "recommendation_rationale": "One sentence: the single decisive reason, distinguishing new money vs existing holders if they differ (e.g. 'Existing holders keep positions for the FY27 capacity ramp, but new money should wait for entry below 24x given unresolved cash conversion').",
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
