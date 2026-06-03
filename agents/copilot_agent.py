"""
agents/copilot_agent.py — Conversational synthesis agent for the Novus Copilot chat

Replaces the old Drafter -> Critic -> 3-retry entailment loop (which routinely
terminated on analytical queries like "red flags" because every insight is a
synthesis that fails literal-substring verification).

Design:
  - Single ReAct agent, same engine that powers the main CIO pipeline
  - Shared tool surface (search_document, get_metric, compute_ratio, ...)
  - Plus 4 memory-layer tools (get_management_inconsistencies, get_thesis_drift,
    get_negative_space_report, get_audit_trail) so the agent can surface
    cross-quarter alpha signals without computing them from scratch
  - No entailment critic. Grounding is enforced by the fact that the agent
    cannot state any hard number it hasn't retrieved via a tool call.
  - Outputs markdown prose inside a tiny JSON envelope so the frontend can
    still receive citations alongside the answer.
"""

import json

from core.agent_base_v3 import AgentV3
from core.tools import build_memory_tools, Tool


class CopilotAgentV3(AgentV3):
    """The Copilot chat agent. One per chat turn (stateless between turns —
    caller supplies history via extraction_signals)."""

    # Chat is short-form. 6 iterations is enough for 3-5 tool calls plus
    # a final JSON turn. If the agent hasn't answered in 6 steps, something
    # is stuck and the forced-final turn will still produce a response.
    MAX_ITERATIONS = 6

    # No second-pass verification. Tool grounding IS the verification.
    VERIFY = False

    @property
    def agent_name(self) -> str:
        return "copilot"

    @property
    def agent_role(self) -> str:
        return (
            "You are Novus Copilot, the conversational synthesis engine for "
            "institutional equity research. You serve portfolio managers at "
            "Indian mutual funds and AMCs. Your one job is to answer the "
            "PM's question with tool-grounded facts and cross-quarter "
            "synthesis — never with memorized or invented numbers.\n\n"
            "HARD RULES:\n"
            "1. You MUST NOT state any hard number (ratio, percentage, amount, "
            "year-over-year change) unless you retrieved it with a tool call in "
            "this conversation. 'The margin was 12%' is a sourced claim only if "
            "you called get_metric or compute_ratio for it.\n"
            "2. When the user asks about 'red flags', 'bear case', 'what's "
            "changed', 'management credibility', 'narrative shifts', 'story "
            "drift', or 'questions for the call' — CALL THE MEMORY TOOLS FIRST: "
            "get_management_inconsistencies, get_negative_space_report, "
            "get_thesis_drift. These surface cross-quarter alpha signals that "
            "no single document chunk contains.\n"
            "3. When the user asks for a number (ROIC, margin, growth, "
            "receivable days, etc.), CALL get_metric or compute_ratio. Never "
            "answer numerical questions from memory.\n"
            "4. When citing a qualitative claim from a transcript, use "
            "search_document and include the returned chunk_id in your "
            "citations[] array.\n"
            "5. If a tool returns empty or data is genuinely unavailable, "
            "say so explicitly. Do NOT fabricate to fill the gap.\n\n"
            "TONE: institutional, concise, alpha-focused. You are not a "
            "tutorial. Surface the PM-actionable insight first, then the "
            "supporting numbers."
        )

    @property
    def output_example(self) -> str:
        # Tiny JSON envelope. The 'answer' field is what the frontend renders
        # as markdown; 'citations' and 'tools_used' are for future hover-to-
        # verify UX and analytics.
        return json.dumps(
            {
                "answer": (
                    "### Three red flags for HINDUNILVR\n\n"
                    "1. **PAT masked by Other Income**. Other Income/PBT rose "
                    "from 8.2% (FY23) to 29.3% (FY24); core operating profit "
                    "flat. [Annual Report FY24, chunk c_8f2a]\n"
                    "2. **Receivables outpacing revenue**. Trade receivables "
                    "+28% YoY vs revenue +2.0%. Receivable days up 14 -> 19. "
                    "[Balance Sheet, FY24]\n"
                    "3. **Narrative shift on rural demand**. Q2 FY26 call "
                    "framed the miss as 'temporary rural slowdown'; Q3 FY26 "
                    "call reframed it as 'structural maturity in mass "
                    "categories' — reason drift without acknowledging the "
                    "pivot. [Management Inconsistency c_811 <-> c_4e2]"
                ),
                "citations": [
                    {"doc_id": "annual_report_fy24.pdf", "chunk_id": "c_8f2a", "page": 42},
                    {"doc_id": "concall_q3_fy26.pdf", "chunk_id": "c_811", "page": 7},
                ],
                "tools_used": [
                    "get_management_inconsistencies",
                    "get_metric",
                    "compute_ratio",
                    "detect_anomaly",
                ],
            },
            indent=2,
        )

    def build_agent_tools(self, doc: str, tables: dict, ticker: str = "") -> list[Tool]:
        """Memory tools layered on top of the 12 shared tools the base class
        already registers via build_shared_tools()."""
        mem_reg = build_memory_tools(ticker)
        # ToolRegistry exposes _tools internally; pull the list and return
        return list(mem_reg._tools.values())

    def build_initial_context(
        self, ticker: str, sector: str, signals: dict, doc_chars: int
    ) -> str:
        """Pack chat history + current question. Does NOT pack the full doc —
        the agent will fetch what it needs via search_document."""
        history = signals.get("_history", []) or []
        question = signals.get("_question", "") or ""
        fiscal_period = signals.get("_fiscal_period", "")

        parts = [
            f"Target company: {ticker} ({sector}).",
        ]
        if fiscal_period:
            parts.append(f"Current fiscal period context: {fiscal_period}.")

        # Recent chat history (last 6 turns) for context
        if history:
            parts.append("\nRecent conversation (most recent last):")
            for m in history[-6:]:
                role = m.get("role", "user").upper()
                content = (m.get("content") or "").strip()
                if not content:
                    continue
                # Trim any verbose prior Copilot responses
                if len(content) > 500:
                    content = content[:500] + "... [trimmed]"
                parts.append(f"  [{role}] {content}")

        parts.append(f"\n## Current user question\n{question}")
        parts.append(
            "\nAnswer using tools. Remember: every hard number must come from "
            "a tool call this turn, and analytical questions should consult the "
            "memory tools first (get_management_inconsistencies, "
            "get_negative_space_report, get_thesis_drift)."
        )
        return "\n".join(parts)
