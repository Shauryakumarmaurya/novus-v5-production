"""
novus_v3/core/agent_base_v3.py — v3 Agent Base Class

Wires together all 5 shifts:
  1. Tool use          → build_agent_tools()
  2. ReAct loop        → react_engine.react_loop()
  3. Self-verification → react_engine.run_verification()
  4. Dynamic prompts   → prompt_composer.compose_prompt()
  5. Audit trail       → AuditTrail dataclass

Subclasses implement:
  - agent_name, agent_role, output_example
  - build_agent_tools()    → register agent-specific tools
  - build_initial_context() → what to tell the model to start investigating
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from core.tools import ToolRegistry, build_shared_tools, Tool
from core.react_engine import react_loop, run_verification, ReActResult
from core.prompt_composer import compose_prompt
from core.llm_client import LLMClient, get_llm_client


@dataclass
class AuditTrail:
    """The v3 agent output — reasoning chain IS the product."""
    agent_name: str
    ticker: str
    sector: str = ""

    # Investigation record
    steps: list[dict] = field(default_factory=list)
    findings: Optional[dict] = None
    data_gaps: list[str] = field(default_factory=list)

    # Verification
    verification: Optional[dict] = None
    verified: bool = False

    # Metadata
    confidence: float = 0.0
    tools_called: int = 0
    llm_calls: int = 0
    execution_time_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "ticker": self.ticker,
            "sector": self.sector,
            "findings": self.findings,
            "data_gaps": self.data_gaps,
            "confidence": self.confidence,
            "verification": self.verification,
            "investigation_depth": {
                "tools_called": self.tools_called,
                "llm_calls": self.llm_calls,
                "steps": len(self.steps),
            },
            "execution_time_s": self.execution_time_s,
        }

    def to_analyst_note(self) -> str:
        """Render as a readable report.
        
        For pm_synthesis: a clean client-facing report with no internal
        architecture leakage — no agent names, no conviction badge, no
        debug metadata.
        
        For other agents: a concise internal-facing summary (used only
        in the live dashboard cards, never in the PDF).
        """
        from utils.formatters import format_dict_as_markdown, _is_empty_or_none

        if self.agent_name == "pm_synthesis":
            return self._render_pm_report()

        # ── Internal agent note (non-PM agents) ──
        from utils.formatters import _clean_human_key
        clean_name = _clean_human_key(self.agent_name)
        lines = [f"## {clean_name}: {self.ticker}"]
        lines.append("")

        if self.findings:
            lines.extend(format_dict_as_markdown(self.findings, indent=0))
            lines.append("")

        if self.data_gaps:
            lines.append("### Data Gaps")
            for g in self.data_gaps:
                lines.append(f"- {g}")
            lines.append("")

        return "\n".join(lines)

    def _render_pm_report(self) -> str:
        """Render the PM Synthesis findings as a clean, structured
        research report suitable for client-facing PDF export.
        
        Key design decisions:
        - No agent name header (the report title is set by the PDF template)
        - No conviction/confidence badge (a bare % destroys trust)
        - Scoreboard items each on their own line with explicit formatting
        - Every null/empty/None field silently suppressed
        - Section titles mapped to professional research-report headings
        """
        from utils.formatters import format_dict_as_markdown, _is_empty_or_none, _clean_human_key

        findings = self.findings or {}
        lines = []

        # ── Section ordering and professional titles ──
        # Maps the PM Synthesis JSON keys to proper report section headings.
        # Order matters — this defines the structure of the report.
        _SECTION_ORDER = [
            ("executive_summary",     "Executive Summary"),
            ("fundamental_analysis",  "Fundamental Analysis"),
            ("forensic_audit",        "Forensic & Accounting Quality"),
            ("capital_allocation",    "Capital Allocation"),
            ("management_quality",    "Management & Governance"),
            ("valuation",             "Valuation & Scenario Analysis"),
            ("forward_estimates",     "Forward Estimates"),
            ("catalyst_calendar",     "Catalyst Calendar"),
            ("bull_case",             "Bull Case"),
            ("bear_case",             "Bear Case"),
            ("variant_perception",    "Variant Perception"),
            ("scoreboard",            "Scoreboard"),
            ("recommendation",        "Recommendation"),
            ("kill_criteria",         "Kill Criteria"),
            ("upside_triggers",       "Upside Triggers"),
            ("evidence_citations",    "Evidence & Citations"),
            ("data_gaps",             "Data Gaps & Limitations"),
        ]

        for key, title in _SECTION_ORDER:
            val = findings.get(key)
            if _is_empty_or_none(val):
                continue

            if key == "scoreboard":
                lines.append(f"## {title}")
                lines.append("")
                self._render_scoreboard(val, lines)
                lines.append("")
            elif key == "recommendation":
                lines.append(f"## {title}: {val}")
                lines.append("")
            elif key == "evidence_citations" and isinstance(val, list):
                lines.append(f"## {title}")
                lines.append("")
                for item in val:
                    if _is_empty_or_none(item): continue
                    if isinstance(item, dict) and "quote" in item and "source" in item:
                        lines.append(f"- *\"{item['quote']}\"* — **{item['source']}**")
                    else:
                        lines.extend(format_dict_as_markdown([item], indent=0))
                lines.append("")
            elif isinstance(val, str):
                lines.append(f"## {title}")
                lines.append("")
                lines.append(val)
                lines.append("")
            elif isinstance(val, list):
                lines.append(f"## {title}")
                lines.append("")
                for item in val:
                    if _is_empty_or_none(item):
                        continue
                    if isinstance(item, dict):
                        if key == "kill_criteria" and "criterion" in item:
                            kc_id = item.get("id", "")
                            kc_id_str = f"[{kc_id}] " if kc_id else ""
                            lines.append(f"- {kc_id_str}{item['criterion']}")
                        else:
                            parts = []
                            for dk, dv in item.items():
                                if not _is_empty_or_none(dv):
                                    parts.append(f"**{_clean_human_key(dk)}:** {dv}")
                            if parts:
                                lines.append(f"- {' | '.join(parts)}")
                    else:
                        lines.append(f"- {item}")
                lines.append("")
            elif isinstance(val, dict):
                lines.append(f"## {title}")
                lines.append("")
                lines.extend(format_dict_as_markdown(val, indent=0))
                lines.append("")

        # ── Render any keys NOT in the section order (future-proofing) ──
        rendered_keys = {k for k, _ in _SECTION_ORDER}
        for key, val in findings.items():
            if key in rendered_keys or _is_empty_or_none(val):
                continue
            title = _clean_human_key(key)
            lines.append(f"## {title}")
            lines.append("")
            if isinstance(val, (dict, list)):
                lines.extend(format_dict_as_markdown(val, indent=0))
            else:
                lines.append(str(val))
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _render_scoreboard(scoreboard: dict, lines: list):
        """Render the scoreboard as a clean, line-separated list.
        
        Fixes the collision bug where 'Management Score: B-' was
        running into 'Moat Durability: WEAKENING' because the formatter
        didn't force line breaks between adjacent items.
        """
        from utils.formatters import _is_empty_or_none, _clean_human_key

        if not isinstance(scoreboard, dict):
            return

        for k, v in scoreboard.items():
            if _is_empty_or_none(v):
                continue
            label = _clean_human_key(k)
            # Format the value — uppercase for grades/verdicts
            display_val = str(v)
            if isinstance(v, (int, float)):
                # Numeric values like implied growth
                if abs(v) < 1:
                    display_val = f"{v:.1%}"
                else:
                    display_val = f"{v}"
            lines.append(f"- **{label}:** {display_val}")


class AgentV3(ABC):
    """
    v3 agent base.
    
    Comparison to your current AgentBase:
    
    Current AgentBase.execute():
        system_prompt = self.build_system_prompt(ticker)       # hardcoded
        raw_response = call_deepseek(system_prompt, context)   # 1 call
        parsed = self.output_model.model_validate_json(resp)   # parse
        citations = self._validate_citations(parsed, context)  # substring check
        return AgentFinding(...)                                # done
    
    AgentV3.execute():
        prompt = compose_prompt(sector, signals, ...)          # dynamic
        tools = shared_tools + agent_tools                     # tool registry
        result = react_loop(prompt, context, tools)            # multi-turn
        verified = run_verification(result, tools)             # critic pass
        return AuditTrail(...)                                 # full trail
    """

    MAX_ITERATIONS = 8
    VERIFY = True

    @property
    @abstractmethod
    def agent_name(self) -> str: ...

    @property
    @abstractmethod
    def agent_role(self) -> str:
        """One-paragraph description of this agent's expertise."""
        ...

    @property
    @abstractmethod
    def output_example(self) -> str:
        """Concrete JSON example of the expected output (NOT a schema dump)."""
        ...

    def build_agent_tools(self, doc: str, tables: dict, ticker: str = "") -> list[Tool]:
        """
        Override to add agent-specific tools beyond the shared set.
        Return a list of Tool objects. They'll be merged into the shared registry.
        """
        return []

    def build_initial_context(
        self, ticker: str, sector: str, signals: dict, doc_chars: int,
    ) -> str:
        """
        Override to customise what the model sees on its first turn.
        Default: brief overview + extraction signals.
        """
        ctx = (
            f"Analyze {ticker} ({sector} sector). "
            f"The document contains {doc_chars:,} characters. "
            f"Use your tools to investigate — do not try to read everything at once."
        )
        # Append extraction signals
        signal_messages = {
            "has_rpt_disclosures":     "⚠️ Significant RPT disclosures detected.",
            "has_contingent_liabilities": "⚠️ Contingent liabilities found.",
            "auditor_changed":         "⚠️ Auditor change detected.",
            "promoter_shares_pledged": "⚠️ Promoter shares pledged.",
            "high_other_income":       "⚠️ Other income appears elevated.",
        }
        for key, msg in signal_messages.items():
            if signals.get(key):
                ctx += f"\n{msg}"
        return ctx

    def execute(
        self,
        ticker: str,
        document_text: str,
        financial_tables: dict,
        sector: str,
        extraction_signals: dict,
        llm: LLMClient = None,
        dynamic_mandate: str = "",
        fiscal_period: str = "",
        on_step=None,
    ) -> AuditTrail:
        """Full v3 execution: compose → investigate → verify → audit trail."""
        start = time.time()

        # ── 1. Compose prompt ──
        output_instruction = (
            "When you have completed your investigation, output your findings "
            "as a JSON object matching this example structure:\n\n"
            f"```json\n{self.output_example}\n```\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "0. YOU ARE STRICTLY FORBIDDEN from executing empty tool calls like {}. Every tool call MUST contain specific, high-intent parameters (e.g., topic, min_year). If you execute an empty tool call, you will be terminated.\n"
            "1. You MUST include a 'source_citation' for every major qualitative claim (e.g., [Q3 Transcript | Page 4] or [RAG Semantic Source]). Any finding without a citation will be severely penalized.\n"
            "2. 'data_gaps' is strictly OPTIONAL. If you found everything you needed, output \"data_gaps\": null. DO NOT invent missing data to fill an array.\n"
            "Only output the JSON when you are confident. Until then, keep investigating."
        )
        system_prompt = compose_prompt(
            agent_name=self.agent_name,
            agent_role=self.agent_role,
            agent_output_instruction=output_instruction,
            sector=sector,
            extraction_signals=extraction_signals,
            ticker=ticker,
        )
        # Inject the dynamic mandate from the Lead Analyst if provided
        if dynamic_mandate:
            system_prompt += f"\n\n## DYNAMIC MANDATE (from Lead Analyst)\n{dynamic_mandate}"

        # ── 2. Build tools ──
        tools = build_shared_tools(document_text, financial_tables, ticker=ticker)
        for extra_tool in self.build_agent_tools(document_text, financial_tables, ticker=ticker):
            tools.register(extra_tool)

        # ── 3. Initial context ──
        initial = self.build_initial_context(
            ticker, sector, extraction_signals, len(document_text),
        )

        # ── 4. ReAct loop ──
        react_result: ReActResult = react_loop(
            system_prompt=system_prompt,
            initial_context=initial,
            tools=tools,
            max_iterations=self.MAX_ITERATIONS,
            llm=llm,
            on_step=on_step,
        )

        # ── 5. Verification ──
        verification = None
        if self.VERIFY and react_result.final_output:
            verification = run_verification(
                findings=react_result.final_output,
                tools=tools,
                llm=llm,
            )

        # ── 6. Compute confidence ──
        confidence = self._compute_confidence(react_result, verification)

        # ── 7. Assemble audit trail ──
        elapsed = round(time.time() - start, 2)
        trail = AuditTrail(
            agent_name=self.agent_name,
            ticker=ticker,
            sector=sector,
            steps=[
                {
                    "action": s.action or "reasoning",
                    "thought": (s.thought or "")[:200],
                    "observation": (s.observation or "")[:200],
                    "input": s.action_input or {},
                }
                for s in react_result.reasoning_chain
            ],
            findings=react_result.final_output,
            data_gaps=(react_result.final_output or {}).get("data_gaps", []),
            verification=verification,
            verified=verification is not None,
            confidence=confidence,
            tools_called=react_result.tools_called,
            llm_calls=react_result.total_llm_calls,
            execution_time_s=elapsed,
        )

        # ── Memory: persist high-confidence investigation patterns ──
        # We only store the tool-sequence footprint (not content) so the memory
        # layer can later suggest proven strategies back to the agent.
        try:
            if confidence >= 0.7 and react_result.reasoning_chain:
                tool_sequence = [
                    s.action for s in react_result.reasoning_chain
                    if s.action
                ]
                if tool_sequence:
                    from core.memory import get_memory
                    get_memory().store_investigation_pattern(
                        agent_name=self.agent_name,
                        ticker=ticker,
                        tool_sequence=tool_sequence,
                        confidence=confidence,
                        fiscal_period=fiscal_period or None,
                    )
        except Exception as e:
            print(f"[AgentV3] Memory store_investigation_pattern failed: {e}")

        return trail

    def _compute_confidence(self, react: ReActResult, verif: Optional[dict]) -> float:
        if react.final_output is None:
            return 0.1
        score = 0.4                                       # base: produced output
        score += min(react.tools_called * 0.07, 0.25)     # investigation depth
        if react.unique_tools_used >= 3:
            score += 0.1                                  # breadth bonus
        if verif:
            rel = verif.get("overall_reliability", 0.5)
            score += 0.25 * rel                           # verification score
            errors = verif.get("critical_errors", [])
            score -= len(errors) * 0.1                    # penalise errors
        return round(max(0.1, min(1.0, score)), 2)
