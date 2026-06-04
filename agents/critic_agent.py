"""
agents/critic_agent.py — V3 Critic Agent (Phase 4: Strict Grounding)

Role: Chief Compliance and Verification Officer.
This agent does NOT generate new analysis. It cross-references every quantitative
claim made by the qualitative agents against the structured financial tables
and RAG-verified document text.

Pipeline position: Runs AFTER reflection, BEFORE PM Synthesis.
"""

import time
import json
from core.agent_base_v3 import AuditTrail, AgentV3
from core.llm_client import LLMClient
from core.tools import build_shared_tools


class CriticAgentV3(AgentV3):
    """
    Overrides AgentV3.execute() to accept peer_findings and run a
    strict verification pass against financial_tables.
    """

    agent_name = "critic_agent"

    agent_role = (
        "You are the Chief Compliance and Verification Officer for an institutional fund. "
        "Your ONLY job is to review the quantitative claims made by the qualitative agents "
        "(Moat, Capital, Narrative, Forensic) and cross-reference them against the structured "
        "financial tables. If an agent hallucinates a metric (e.g., claims 13M stores when "
        "the text says 9M, or claims 5.9% ROIC when Invested Capital is skewed), you MUST "
        "flag and correct it. You do NOT generate new analysis — you only verify."
    )

    # Canonical metric_category taxonomy the Critic is instructed to use.
    # Keep in sync with core/memory.py::METRIC_TAXONOMY.
    METRIC_TAXONOMY = [
        "margins", "revenue_growth", "volume_growth", "roic", "roce",
        "cash_flow", "working_capital", "leverage", "capex",
        "guidance", "demand_commentary", "pricing_power",
        "governance", "promoter_pledge", "rpt", "auditor",
        "capital_allocation", "mna", "dividend_policy",
        "distribution_reach", "regional_revenue", "segments",
        "product_mix", "competitive_position",
        "compliance", "litigation",
    ]

    output_example = json.dumps({
        "corrections": [
            {
                "agent_name": "moat_architect",
                "metric_category": "distribution_reach",
                "fiscal_period": "FY24",
                "original_claim": "Distribution reach of 13M stores",
                "verified_fact": "Distribution reach of 9M stores",
                "citations": [
                    {
                        "doc_id": "annual_report_fy24.pdf",
                        "chunk_id": "c_8f2a",
                        "snippet": "...company serves approximately 9 million retail outlets across India...",
                        "page": 42
                    }
                ],
                "source_citation": "[Annual Report FY24 | Page 42]",
                "action": "CORRECTED",
                "confidence": 0.95
            },
            {
                "agent_name": "capital_allocator",
                "metric_category": "dividend_policy",
                "fiscal_period": "FY24",
                "original_claim": "Dividend payout 95% signals inability to invest",
                "verified_fact": "Dividend payout 95% is standard for FMCG; growth is Opex-driven",
                "citations": [
                    {"doc_id": "sector_benchmark", "chunk_id": None, "snippet": "", "page": None}
                ],
                "source_citation": "[Sector Benchmark]",
                "action": "CORRECTED",
                "confidence": 0.85
            }
        ],
        "unverifiable_claims": [
            {
                "agent_name": "narrative_decoder",
                "metric_category": "guidance",
                "fiscal_period": "Q3_FY26",
                "claim": "Management guided for 8% volume growth",
                "reason": "No matching text found in concall transcript or quarterly results",
                "action": "FLAGGED_AS_DATA_GAP"
            }
        ],
        "narrative_inconsistencies": [
            {
                "metric_category": "demand_commentary",
                "fiscal_period_a": "Q2_FY26",
                "fiscal_period_b": "Q3_FY26",
                "fact_a": "Management blamed temporary supply chain issues",
                "fact_b": "Management acknowledged structural demand softness",
                "inconsistency_type": "REASON_DRIFT",
                "severity": "HIGH",
                "rationale": "Narrative reversed from transient to structural without acknowledging the pivot."
            }
        ],
        "verification_status": "CLEARED WITH CORRECTIONS",
        "data_gaps": None
    }, indent=2)

    # Allow more iterations — accuracy > speed
    MAX_ITERATIONS = 15
    VERIFY = False  # The critic IS the verifier; no need to self-verify

    def execute(
        self,
        ticker: str,
        document_text: str,
        financial_tables: dict,
        sector: str = "",
        extraction_signals: dict = None,
        peer_findings: dict = None,
        llm: LLMClient = None,
        dynamic_mandate: str = "",
        fiscal_period: str = "",
    ) -> AuditTrail:
        """
        Override: Accepts peer_findings (dict of agent_name -> findings)
        and cross-references every quantitative claim against financial_tables.
        """
        start = time.time()
        extraction_signals = extraction_signals or {}
        peer_findings = peer_findings or extraction_signals.get("peer_findings", {})

        if not peer_findings:
            print("> [CRITIC] ⚠️ No peer findings to verify. Passing through.")
            return AuditTrail(
                agent_name=self.agent_name,
                ticker=ticker,
                sector=sector,
                findings={"corrections": [], "verification_status": "NO_FINDINGS_TO_VERIFY"},
                data_gaps=[],
                confidence=1.0,
                execution_time_s=round(time.time() - start, 2),
                steps=[],
            )

        # ── Phase 2: Deterministic Chronology Verification ──
        from utils.temporal_logic import verify_chronology

        def _scan_for_temporal_hallucinations(data):
            """Recursively search peer findings for causal event dates."""
            if isinstance(data, dict):
                # If we hit a causal block, verify it
                if "cause_date" in data and "effect_date" in data:
                    cause, effect = data["cause_date"], data["effect_date"]
                    # If verification fails, return the offending trace
                    if not verify_chronology(cause, effect):
                        return f"Chronological Hallucination: Claimed cause in {cause} resulted in effect in {effect}."
                for v in data.values():
                    res = _scan_for_temporal_hallucinations(v)
                    if res: return res
            elif isinstance(data, list):
                for item in data:
                    res = _scan_for_temporal_hallucinations(item)
                    if res: return res
            return None

        hallucination_err = _scan_for_temporal_hallucinations(peer_findings)
        if hallucination_err:
            print(f"> [CRITIC] 🚨 TIMELINE REJECTION TRIGGERED: {hallucination_err}")
            return AuditTrail(
                agent_name=self.agent_name,
                ticker=ticker,
                sector=sector,
                findings={
                    "corrections": [],
                    "verification_status": "REJECTED_TIMELINE_HALLUCINATION",
                    "rejection_reason": hallucination_err
                },
                data_gaps=[],
                confidence=0.0,
                execution_time_s=round(time.time() - start, 2),
                steps=[{"action": "temporal_verification", "observation": hallucination_err}],
            )

        # ── Build the verification task prompt ──
        peer_summary = json.dumps(peer_findings, indent=2, default=str)

        taxonomy_str = ", ".join(self.METRIC_TAXONOMY)

        task_prompt = f"""You are the Chief Compliance and Verification Officer for an institutional equity fund.

## YOUR MANDATE
Review ALL quantitative claims below from our specialist analysts. For each hard number 
(store counts, margin percentages, growth rates, ROIC, distribution reach, etc.), 
use your tools to verify it against the structured financial tables or the document text.

## PEER ANALYST FINDINGS TO VERIFY
```json
{peer_summary}
```

## VERIFICATION RULES (NON-NEGOTIABLE)
1. For every hard metric, call `get_metric` or `search_document` to find the source.
2. If the number EXACTLY matches a table value or document passage, mark as VERIFIED.
3. If the number is WRONG (e.g., agent says 13M stores but source says 9M), mark as CORRECTED with the correct value and source citation.
4. If the number CANNOT be found in any source, mark as FLAGGED_AS_DATA_GAP.
5. Do NOT invent corrections. Only correct what you can prove is wrong.
6. Focus on the MOST MATERIAL claims first: ROIC, revenue growth, distribution reach, margins, debt levels.

## REQUIRED FIELDS PER CORRECTION / UNVERIFIABLE CLAIM
Every item in `corrections[]` and `unverifiable_claims[]` MUST include:

- `metric_category` — one of: {taxonomy_str}. Pick the single best match.
  This drives institutional-memory deduplication; arbitrary strings will be rejected.
- `fiscal_period` — the period the claim refers to, formatted as "Q{{1-4}}_FY{{YY}}" (e.g. "Q3_FY26")
  for quarterly or "FY{{YY}}" (e.g. "FY24") for annual. If the claim spans multiple periods,
  use the most recent one.
- `citations` — list of structured provenance objects when the fact is grounded in a document:
    [{{"doc_id": "<source file or ChromaDB doc id>",
       "chunk_id": "<chunk id returned by search_document if quoting a chunk>",
       "snippet": "<up to ~200 chars of the exact source text>",
       "page": <int or null>}}]
  Use an empty list `[]` for claims grounded outside documents (e.g. sector benchmarks).
  `source_citation` (string) is kept for legacy display but `citations` is the authoritative field.
- `confidence` (float 0.0-1.0) — your conviction in this correction. Only corrections with
  confidence >= 0.8 will be injected into future agent prompts, so set this carefully.

## NARRATIVE INCONSISTENCIES (ALPHA SIGNAL)
The following narrative inconsistencies have been detected across prior fiscal periods by the
institutional memory layer (they are not generated by you). You MUST echo them verbatim in your
output under the top-level `narrative_inconsistencies[]` key so PM Synthesis sees them as
first-class findings. Do NOT invent new inconsistencies and do NOT silently drop the ones already
flagged.

{f"ADDITIONAL MANDATE: {dynamic_mandate}" if dynamic_mandate else ""}

Output your findings as JSON matching the schema in your instructions."""

        # ── Build tools ──
        tools = build_shared_tools(document_text, financial_tables, ticker=ticker)
        for extra_tool in self.build_agent_tools(document_text, financial_tables, ticker=ticker):
            tools.register(extra_tool)

        # ── Compose system prompt via base class machinery ──
        from core.prompt_composer import compose_prompt
        output_instruction = (
            "When you have completed your verification, output your findings "
            "as a JSON object matching this example structure:\n\n"
            f"```json\n{self.output_example}\n```\n\n"
            "CRITICAL: Only output the JSON when you have verified all material claims. "
            "Until then, keep investigating with your tools."
        )
        system_prompt = compose_prompt(
            agent_name=self.agent_name,
            agent_role=self.agent_role,
            agent_output_instruction=output_instruction,
            sector=sector,
            extraction_signals=extraction_signals,
            ticker=ticker,
        )
        if dynamic_mandate:
            system_prompt += f"\n\n## DYNAMIC MANDATE (from Lead Analyst)\n{dynamic_mandate}"

        # ── ReAct loop ──
        from core.react_engine import react_loop, ReActResult
        react_result: ReActResult = react_loop(
            system_prompt=system_prompt,
            initial_context=task_prompt,
            tools=tools,
            max_iterations=self.MAX_ITERATIONS,
            llm=llm,
        )

        # ── Compute confidence ──
        confidence, _ = self._compute_confidence(react_result, None, document_text, financial_tables)

        # ── Assemble audit trail ──
        elapsed = round(time.time() - start, 2)
        trail = AuditTrail(
            agent_name=self.agent_name,
            ticker=ticker,
            sector=sector,
            steps=[
                {
                    "action": s.action or "verification",
                    "thought": (s.thought or "")[:200],
                    "observation": (s.observation or "")[:200],
                }
                for s in react_result.reasoning_chain
            ],
            findings=react_result.final_output,
            data_gaps=(react_result.final_output or {}).get("data_gaps") or [],
            verification=None,
            verified=True,  # The critic IS the verification
            confidence=confidence,
            tools_called=react_result.tools_called,
            llm_calls=react_result.total_llm_calls,
            execution_time_s=elapsed,
        )

        corrections = (react_result.final_output or {}).get("corrections", [])
        status = (react_result.final_output or {}).get("verification_status", "UNKNOWN")
        print(f"> [CRITIC] Verification complete: {len(corrections)} corrections. Status: {status} ({elapsed}s)")

        return trail
