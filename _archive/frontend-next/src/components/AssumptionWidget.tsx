"use client";

import { useState, useCallback } from "react";
import { useReportStore } from "@/store/useReportStore";
import type { AuditTask, BaselineAssumption } from "@/store/useReportStore";

// ─── Icons (inline SVG to avoid extra deps) ─────────────────────────────────

const CheckIcon = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
  </svg>
);

const SpinnerIcon = () => (
  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
  </svg>
);

// ─── Component ───────────────────────────────────────────────────────────────

export default function AssumptionWidget() {
  const auditPlan = useReportStore((s) => s.auditPlan);
  const assumptions = useReportStore((s) => s.assumptions);
  const isExecuting = useReportStore((s) => s.isExecuting);
  const executePlan = useReportStore((s) => s.executePlan);
  const ticker = useReportStore((s) => s.ticker);

  // ── Local toggle state for plan tasks ──
  const [enabledTasks, setEnabledTasks] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    auditPlan.forEach((t) => (init[t.agent_id] = true));
    return init;
  });

  // ── Local editable assumption overrides ──
  const [overrides, setOverrides] = useState<Record<string, string>>({});

  const toggleTask = useCallback((agentId: string) => {
    setEnabledTasks((prev) => ({ ...prev, [agentId]: !prev[agentId] }));
  }, []);

  const updateOverride = useCallback((id: string, value: string) => {
    setOverrides((prev) => ({ ...prev, [id]: value }));
  }, []);

  const handleExecute = useCallback(() => {
    // Filter to only enabled tasks
    const approvedPlan = auditPlan.filter((t) => enabledTasks[t.agent_id]);

    // Merge overrides into assumptions
    const humanAssumptions: BaselineAssumption[] = assumptions.map((a) => ({
      ...a,
      human_override: overrides[a.id] ?? a.human_override,
    }));

    executePlan(approvedPlan, humanAssumptions);
  }, [auditPlan, assumptions, enabledTasks, overrides, executePlan]);

  // ── Separate assumptions by category for compact display ──
  const valuationAssumptions = assumptions.filter(
    (a) => a.category === "valuation" || a.category === "growth"
  );
  const otherAssumptions = assumptions.filter(
    (a) => a.category !== "valuation" && a.category !== "growth"
  );

  return (
    <div className="bg-slate-900 border border-slate-700/60 rounded-xl overflow-hidden shadow-2xl shadow-black/40">
      {/* ── Header ── */}
      <div className="px-4 py-3 border-b border-slate-700/50 bg-slate-800/50">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-xs font-semibold tracking-widest uppercase text-slate-400">
            Audit Configuration
          </span>
          <span className="ml-auto text-[10px] font-mono text-slate-500">
            {ticker}
          </span>
        </div>
      </div>

      <div className="p-4 space-y-5 max-h-[520px] overflow-y-auto scrollbar-thin scrollbar-track-slate-900 scrollbar-thumb-slate-700">
        {/* ── Execution Plan Toggle List ── */}
        <section>
          <h3 className="text-[11px] font-semibold tracking-wider uppercase text-slate-500 mb-2.5">
            Agent Execution Plan
          </h3>
          <div className="space-y-1">
            {auditPlan.map((task: AuditTask) => (
              <button
                key={task.agent_id}
                onClick={() => toggleTask(task.agent_id)}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-all duration-150 hover:bg-slate-800/70 group"
              >
                {/* Checkbox */}
                <div
                  className={`w-5 h-5 rounded flex items-center justify-center border transition-all duration-150 flex-shrink-0 ${
                    enabledTasks[task.agent_id]
                      ? "bg-emerald-500/20 border-emerald-500 text-emerald-400"
                      : "border-slate-600 text-transparent group-hover:border-slate-500"
                  }`}
                >
                  <CheckIcon />
                </div>
                {/* Label */}
                <div className="min-w-0 flex-1">
                  <p
                    className={`text-sm font-medium truncate transition-colors ${
                      enabledTasks[task.agent_id]
                        ? "text-slate-200"
                        : "text-slate-500 line-through"
                    }`}
                  >
                    {task.label}
                  </p>
                  <p className="text-[10px] text-slate-500 truncate">
                    {task.description}
                  </p>
                </div>
                {/* Order badge */}
                <span className="text-[10px] font-mono text-slate-600 flex-shrink-0">
                  #{task.order + 1}
                </span>
              </button>
            ))}
          </div>
        </section>

        {/* ── Valuation Assumptions (Editable Inputs) ── */}
        {valuationAssumptions.length > 0 && (
          <section>
            <h3 className="text-[11px] font-semibold tracking-wider uppercase text-slate-500 mb-2.5">
              Key Assumptions
            </h3>
            <div className="grid grid-cols-2 gap-2">
              {valuationAssumptions.map((a) => (
                <div
                  key={a.id}
                  className="bg-slate-800/50 border border-slate-700/40 rounded-lg p-3"
                >
                  <label className="block text-[10px] font-medium text-slate-400 uppercase tracking-wider mb-1.5">
                    {a.statement}
                  </label>
                  {a.editable ? (
                    <input
                      type="number"
                      step="0.1"
                      className="w-full bg-slate-900 border border-slate-600/50 rounded-md px-2.5 py-1.5 text-sm font-mono text-emerald-400 focus:outline-none focus:border-emerald-500/60 focus:ring-1 focus:ring-emerald-500/20 transition-all"
                      defaultValue={a.human_override ?? ""}
                      placeholder={`${(a.confidence * 100).toFixed(0)}% conf`}
                      onChange={(e) => updateOverride(a.id, e.target.value)}
                    />
                  ) : (
                    <p className="text-sm font-mono text-slate-300">
                      {a.human_override ?? `${(a.confidence * 100).toFixed(1)}%`}
                    </p>
                  )}
                  <p className="text-[9px] text-slate-600 mt-1 truncate">
                    Source: {a.source}
                  </p>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ── Other Assumptions (Read-only chips) ── */}
        {otherAssumptions.length > 0 && (
          <section>
            <h3 className="text-[11px] font-semibold tracking-wider uppercase text-slate-500 mb-2">
              Context Assumptions
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {otherAssumptions.map((a) => (
                <span
                  key={a.id}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-slate-800/60 border border-slate-700/40 text-[11px] text-slate-400"
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      a.confidence > 0.7
                        ? "bg-emerald-500"
                        : a.confidence > 0.4
                        ? "bg-amber-500"
                        : "bg-red-500"
                    }`}
                  />
                  {a.statement}
                </span>
              ))}
            </div>
          </section>
        )}
      </div>

      {/* ── Footer: Execute Button ── */}
      <div className="px-4 py-3 border-t border-slate-700/40 bg-slate-800/30">
        <button
          onClick={handleExecute}
          disabled={isExecuting || auditPlan.filter((t) => enabledTasks[t.agent_id]).length === 0}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold transition-all duration-200 disabled:opacity-40 disabled:cursor-not-allowed bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white shadow-lg shadow-emerald-900/30 active:scale-[0.98]"
        >
          {isExecuting ? (
            <>
              <SpinnerIcon />
              Executing Pipeline…
            </>
          ) : (
            <>
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
              Approve &amp; Execute ({auditPlan.filter((t) => enabledTasks[t.agent_id]).length} agents)
            </>
          )}
        </button>
      </div>
    </div>
  );
}
