"""
novus_v3/core/react_engine.py — ReAct Reasoning Loop

The beating heart of v3. Replaces single-shot LLM calls with a
multi-turn investigation loop:

    THINK → ACT (call tool) → OBSERVE (read result) → THINK → ...

Preserves the full reasoning chain as an audit trail.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

from core.llm_client import LLMClient, LLMResponse, get_llm_client
from core.tools import ToolRegistry


# ═══════════════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ReasoningStep:
    """One step in the model's investigation."""
    step: int
    thought: str = ""                    # What the model was reasoning about
    action: Optional[str] = None         # Tool name called
    action_input: Optional[dict] = None  # Tool arguments
    observation: Optional[str] = None    # Tool result
    latency_ms: int = 0


@dataclass
class ReActResult:
    """Complete output of a reasoning session."""
    final_output: Optional[dict] = None
    raw_final_text: str = ""
    reasoning_chain: list[ReasoningStep] = field(default_factory=list)
    tools_called: int = 0
    unique_tools_used: int = 0
    total_llm_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    execution_time_s: float = 0.0
    hit_max_iterations: bool = False

    @property
    def investigation_depth(self) -> str:
        if self.tools_called >= 6:
            return "deep"
        if self.tools_called >= 3:
            return "moderate"
        if self.tools_called >= 1:
            return "shallow"
        return "none"


# ═══════════════════════════════════════════════════════════════════════════
# ReAct Loop
# ═══════════════════════════════════════════════════════════════════════════

def react_loop(
    system_prompt: str,
    initial_context: str,
    tools: ToolRegistry,
    max_iterations: int = 8,
    max_tool_result_chars: int = 3000,
    llm: LLMClient = None,
    on_step: Optional[Callable[[ReasoningStep], None]] = None,
) -> ReActResult:
    """
    Multi-turn ReAct loop.
    
    The model gets the system prompt + initial context on turn 1,
    then iteratively calls tools and reasons until it produces
    a final JSON answer.
    
    Args:
        system_prompt:    Agent-specific instructions
        initial_context:  Brief overview + extraction signals (NOT the full doc)
        tools:            ToolRegistry with available investigation tools
        max_iterations:   Safety cap on reasoning steps
        max_tool_result_chars: Truncate tool outputs to prevent context blowup
        llm:              LLMClient instance (uses default if None)
        on_step:          Optional callback fired after each reasoning step.
                          Used by the Copilot chat endpoint to stream progress
                          to SSE clients in real time. Any exception inside the
                          callback is swallowed so progress reporting cannot
                          break the loop itself.
    """
    if llm is None:
        llm = get_llm_client()

    start = time.time()
    chain: list[ReasoningStep] = []
    tool_names_used: set[str] = set()
    total_in = 0
    total_out = 0

    def _notify(s: ReasoningStep) -> None:
        """Fire the on_step callback if provided. Swallow exceptions so a
        flaky progress observer cannot break the reasoning loop."""
        if on_step is None:
            return
        try:
            on_step(s)
        except Exception as exc:
            print(f"  [ReAct] on_step callback raised (ignored): {exc}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": initial_context},
    ]
    tool_defs = tools.to_api_format()

    for iteration in range(1, max_iterations + 1):
        # ── Budget pressure: warn the model it's running out of turns ──
        if iteration == max_iterations - 1:
            messages.append({
                "role": "user",
                "content": (
                    "⚠️ BUDGET WARNING: You have 1 investigation step remaining. "
                    "You MUST produce your final JSON output on the NEXT turn. "
                    "Stop calling tools and synthesize your findings NOW."
                ),
            })
        elif iteration == max_iterations:
            # Force final answer: call LLM WITHOUT tools so it can't request more
            messages.append({
                "role": "user",
                "content": (
                    "🚨 FINAL TURN: You have used all investigation budget. "
                    "Output your complete findings as a JSON object NOW. "
                    "Do NOT request any more tools. Just output the JSON."
                ),
            })
            resp = llm.call(messages=messages, tools=None, max_tokens=None)
            total_in += resp.input_tokens
            total_out += resp.output_tokens
            print(f"  [ReAct] FORCED FINAL (no tools) | content_len={len(resp.content)}")

            step = ReasoningStep(
                step=iteration,
                thought=resp.thinking or "",
                latency_ms=resp.latency_ms,
            )
            chain.append(step)
            _notify(step)
            final_output = _extract_json(resp.content)
            if final_output:
                print(f"  [ReAct] ✅ Forced final produced valid JSON!")
            return ReActResult(
                final_output=final_output,
                raw_final_text=resp.content,
                reasoning_chain=chain,
                tools_called=sum(1 for s in chain if s.action),
                unique_tools_used=len(tool_names_used),
                total_llm_calls=iteration,
                total_input_tokens=total_in,
                total_output_tokens=total_out,
                execution_time_s=round(time.time() - start, 2),
                hit_max_iterations=True,
            )

        # Call the LLM
        resp = llm.call(messages=messages, tools=tool_defs)
        total_in += resp.input_tokens
        total_out += resp.output_tokens

        # ── Diagnostic logging ──
        print(f"  [ReAct] Iter {iteration}/{max_iterations} | "
              f"finish={resp.finish_reason} | tool_calls={len(resp.tool_calls)} | "
              f"content_len={len(resp.content)}")

        step = ReasoningStep(
            step=iteration,
            thought=resp.thinking or "",
            latency_ms=resp.latency_ms,
        )

        if resp.has_tool_calls:
            # ── Model wants to investigate something ──
            # Process each tool call (usually 1, sometimes 2-3)
            assistant_msg = {"role": "assistant", "content": resp.content}
            if resp.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"]),
                        },
                    }
                    for tc in resp.tool_calls
                ]
            messages.append(assistant_msg)

            for tc in resp.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]

                # Execute the tool
                result = tools.execute(tool_name, tool_args)
                # Truncate to prevent context explosion
                if len(result) > max_tool_result_chars:
                    result = result[:max_tool_result_chars] + "\n... [TRUNCATED]"

                tool_names_used.add(tool_name)
                step.action = tool_name
                step.action_input = tool_args
                step.observation = result[:500]  # Store summary in chain

                # Feed result back to model
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

            chain.append(step)
            _notify(step)
            continue

        # ── No tool calls → model produced final answer ──
        chain.append(step)
        _notify(step)

        # Parse the final JSON
        final_output = _extract_json(resp.content)

        return ReActResult(
            final_output=final_output,
            raw_final_text=resp.content,
            reasoning_chain=chain,
            tools_called=sum(1 for s in chain if s.action),
            unique_tools_used=len(tool_names_used),
            total_llm_calls=iteration,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            execution_time_s=round(time.time() - start, 2),
        )

    # ── Hit max iterations without final answer ──
    print(f"  [ReAct] ⚠️ HIT MAX ITERATIONS ({max_iterations}). No final JSON produced.")
    # Attempt last-resort extraction from the last content
    last_content = messages[-1].get("content", "") if isinstance(messages[-1], dict) else ""
    fallback = _extract_json(last_content)
    if fallback:
        print(f"  [ReAct] ✅ Recovered JSON from last message.")
    return ReActResult(
        final_output=fallback,
        raw_final_text="[MAX ITERATIONS] Model did not produce a final answer.",
        reasoning_chain=chain,
        tools_called=sum(1 for s in chain if s.action),
        unique_tools_used=len(tool_names_used),
        total_llm_calls=max_iterations,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        execution_time_s=round(time.time() - start, 2),
        hit_max_iterations=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Verification Pass (Shift 3)
# ═══════════════════════════════════════════════════════════════════════════

VERIFIER_PROMPT = """You are a SKEPTICAL AUDITOR reviewing another analyst's work.

RULES:
1. For each finding, search the document independently to verify.
2. Use compute_ratio to recheck any numerical claim.
3. Actively look for CONTRADICTORY evidence.
4. Grade: VERIFIED | WEAK | UNSUPPORTED | CONTRADICTED

Output JSON:
{
  "checks": [
    {
      "claim": "Original claim being verified",
      "status": "VERIFIED|WEAK|UNSUPPORTED|CONTRADICTED",
      "supporting_evidence": "What confirms it",
      "contradicting_evidence": "What opposes it or null",
      "corrected_claim": "Revised version if needed or null"
    }
  ],
  "overall_reliability": 0.0 to 1.0,
  "critical_errors": []
}"""


def run_verification(
    findings: dict,
    tools: ToolRegistry,
    max_iterations: int = 5,
    llm: LLMClient = None,
) -> dict:
    """
    Second pass: a skeptical persona verifies the first pass's findings.
    Same tools, different objective.
    """
    context = (
        "Verify the following analyst findings. USE the tools to independently "
        "check each claim. Do NOT trust the original analysis.\n\n"
        f"```json\n{json.dumps(findings, indent=2, ensure_ascii=False)}\n```"
    )

    result = react_loop(
        system_prompt=VERIFIER_PROMPT,
        initial_context=context,
        tools=tools,
        max_iterations=max_iterations,
        llm=llm,
    )
    return result.final_output or {"overall_reliability": 0.5, "checks": [], "critical_errors": ["Verification failed"]}


# ═══════════════════════════════════════════════════════════════════════════
# JSON extraction helper
# ═══════════════════════════════════════════════════════════════════════════

def _extract_json(text: str) -> Optional[dict]:
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "```json" in text:
        text = text.split("```json", 1)[1].rsplit("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].rsplit("```", 1)[0]
    else:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            text = m.group(0)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        print(f"  [ReAct] ❌ JSON Parse Error: {e}")
        print(f"  [ReAct] ❌ Raw text snippet: {text.strip()[:500]} ... {text.strip()[-500:]}")
        return None
