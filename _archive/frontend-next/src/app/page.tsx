"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { useReportStore } from "@/store/useReportStore";
import AssumptionWidget from "@/components/AssumptionWidget";
import ModularAnalysisCard from "@/components/ModularAnalysisCard";

// ─── Agent Label Map ─────────────────────────────────────────────────────────

const AGENT_LABELS: Record<string, string> = {
  TRIAGE: "Triage Kill Screen",
  FSA_QUANT: "FSA Quantitative Analysis",
  fsa_quant: "FSA Quantitative Analysis",
  FORENSIC_INVESTIGATOR: "Forensic Investigation",
  forensic_investigator: "Forensic Investigation",
  MOAT_ARCHITECT: "Competitive Moat Architecture",
  moat_architect: "Competitive Moat Architecture",
  CAPITAL_ALLOCATOR: "Capital Allocation Analysis",
  capital_allocator: "Capital Allocation Analysis",
  NARRATIVE_DECODER: "Narrative & Mgmt Decoder",
  narrative_decoder: "Narrative & Mgmt Decoder",
  NLP_ANALYST: "NLP Sentiment Analysis",
  nlp_analyst: "NLP Sentiment Analysis",
  PM_SYNTHESIS: "Portfolio Manager Synthesis",
  pm_synthesis: "Portfolio Manager Synthesis",
};

// ─── Chat Message Type ───────────────────────────────────────────────────────

interface ChatMessage {
  id: string;
  role: "user" | "system" | "widget";
  content: string;
  timestamp: Date;
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function ResearchDashboard() {
  // ── Store ──
  const ticker = useReportStore((s) => s.ticker);
  const query = useReportStore((s) => s.query);
  const hasStarted = useReportStore((s) => s.hasStarted);
  const showAssumptions = useReportStore((s) => s.showAssumptions);
  const isInitiating = useReportStore((s) => s.isInitiating);
  const isExecuting = useReportStore((s) => s.isExecuting);
  const initError = useReportStore((s) => s.initError);
  const executeError = useReportStore((s) => s.executeError);
  const auditPlan = useReportStore((s) => s.auditPlan);
  const reportCards = useReportStore((s) => s.reportCards);
  const pipelineDuration = useReportStore((s) => s.pipelineDuration);
  const totalAgentsCompleted = useReportStore((s) => s.totalAgentsCompleted);
  const initiateResearch = useReportStore((s) => s.initiateResearch);
  const reset = useReportStore((s) => s.reset);

  // ── Local state ──
  const [tickerInput, setTickerInput] = useState("");
  const [queryInput, setQueryInput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      id: "sys-0",
      role: "system",
      content:
        "Welcome to Novus. Enter a ticker symbol and research query to begin your institutional analysis.",
      timestamp: new Date(),
    },
  ]);

  const chatEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, showAssumptions]);

  // ── Handle Initiate (ONLY calls initiateResearch — never executePlan) ──
  const handleInitiate = useCallback(() => {
    if (!tickerInput.trim() || !queryInput.trim()) return;

    const t = tickerInput.toUpperCase().trim();
    const q = queryInput.trim();

    setChatMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        role: "user",
        content: `Analyze **${t}**: ${q}`,
        timestamp: new Date(),
      },
      {
        id: `sys-init-${Date.now()}`,
        role: "system",
        content: `Initiating research pipeline for ${t}… Building strategic audit plan via DeepSeek R1.`,
        timestamp: new Date(),
      },
    ]);

    initiateResearch(t, q);
  }, [tickerInput, queryInput, initiateResearch]);

  // ── Inject chat message when plan arrives ──
  useEffect(() => {
    if (showAssumptions && auditPlan.length > 0) {
      setChatMessages((prev) => {
        // Don't inject twice
        if (prev.some((m) => m.id === "sys-plan-ready")) return prev;
        return [
          ...prev,
          {
            id: "sys-plan-ready",
            role: "system",
            content: `✅ Audit plan generated with ${auditPlan.length} agents. Review the plan and assumptions below, then click "Approve & Execute" to proceed.`,
            timestamp: new Date(),
          },
        ];
      });
    }
  }, [showAssumptions, auditPlan]);

  // ── Inject chat message when execution starts ──
  useEffect(() => {
    if (isExecuting) {
      setChatMessages((prev) => {
        if (prev.some((m) => m.id === "sys-exec-start")) return prev;
        return [
          ...prev,
          {
            id: "sys-exec-start",
            role: "system",
            content: `⚡ Pipeline executing — agents are processing in parallel. Watch the canvas for real-time results.`,
            timestamp: new Date(),
          },
        ];
      });
    }
  }, [isExecuting]);

  // ── Inject chat message on pipeline completion ──
  useEffect(() => {
    if (pipelineDuration !== null) {
      setChatMessages((prev) => {
        if (prev.some((m) => m.id === "sys-pipeline-done")) return prev;
        return [
          ...prev,
          {
            id: "sys-pipeline-done",
            role: "system",
            content: `✅ Pipeline complete in ${pipelineDuration.toFixed(1)}s. ${totalAgentsCompleted} agents delivered findings.`,
            timestamp: new Date(),
          },
        ];
      });
    }
  }, [pipelineDuration, totalAgentsCompleted]);

  // ── Handle Reset ──
  const handleReset = useCallback(() => {
    reset();
    setTickerInput("");
    setQueryInput("");
    setChatMessages([
      {
        id: "sys-reset",
        role: "system",
        content: "Session reset. Ready for new analysis.",
        timestamp: new Date(),
      },
    ]);
  }, [reset]);

  // ── Derive active agents for the canvas ──
  const activeAgents = Object.keys(reportCards);
  const totalAgents = activeAgents.length;
  const completedAgents = activeAgents.filter(
    (k) => reportCards[k].status === "success"
  ).length;
  const erroredAgents = activeAgents.filter(
    (k) => reportCards[k].status === "error"
  ).length;
  const processingAgents = activeAgents.filter(
    (k) => reportCards[k].status === "processing"
  ).length;

  // ── Derive pipeline status for the live sidebar ──
  const getPipelineStepStatus = (agentId: string) => {
    const card = reportCards[agentId];
    if (!card) return "pending";
    return card.status; // "pending" | "processing" | "success" | "error"
  };

  return (
    <div className="flex h-screen bg-slate-950 text-slate-200 overflow-hidden">
      {/* ══════════════════════════════════════════════════════════════════════
       *  LEFT PANE: 70% — Analysis Canvas
       * ══════════════════════════════════════════════════════════════════════ */}
      <main className="w-[70%] flex flex-col border-r border-slate-800/60">
        {/* ── Top Bar ── */}
        <header className="flex items-center justify-between px-6 py-3 border-b border-slate-800/50 bg-slate-900/40 backdrop-blur-sm">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center">
                <span className="text-xs font-bold text-white">N</span>
              </div>
              <h1 className="text-sm font-bold tracking-tight">
                <span className="text-slate-200">Novus</span>
                <span className="text-slate-500 ml-1 font-normal">
                  Quant Canvas
                </span>
              </h1>
            </div>

            {ticker && (
              <div className="flex items-center gap-2 ml-4 pl-4 border-l border-slate-700/40">
                <span className="text-xs font-mono font-bold text-emerald-400">
                  {ticker}
                </span>
                {hasStarted && (
                  <span className="text-[10px] font-mono text-slate-500">
                    {completedAgents}/{totalAgents} agents
                    {erroredAgents > 0 && (
                      <span className="text-red-400 ml-1">
                        ({erroredAgents} err)
                      </span>
                    )}
                  </span>
                )}
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            {pipelineDuration !== null && (
              <span className="text-[10px] font-mono text-slate-500">
                ⏱ {pipelineDuration.toFixed(1)}s
              </span>
            )}
            {hasStarted && (
              <button
                onClick={handleReset}
                className="px-3 py-1.5 text-[11px] text-slate-500 hover:text-slate-300 bg-slate-800/40 hover:bg-slate-800/70 border border-slate-700/30 rounded-lg transition-all"
              >
                Reset
              </button>
            )}
          </div>
        </header>

        {/* ── Canvas Area ── */}
        <div className="flex-1 overflow-y-auto p-6 scrollbar-thin scrollbar-track-transparent scrollbar-thumb-slate-800">
          {!hasStarted ? (
            /* ── Empty State ── */
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-emerald-500/10 to-teal-600/10 border border-emerald-500/20 flex items-center justify-center mb-6">
                <svg
                  className="w-7 h-7 text-emerald-500/60"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={1.5}
                    d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
                  />
                </svg>
              </div>
              <h2 className="text-lg font-semibold text-slate-300 mb-2">
                Institutional Research Canvas
              </h2>
              <p className="text-sm text-slate-500 max-w-md">
                Agent findings will appear here as modular analysis cards.
                Use the Copilot panel on the right to initiate a research query.
              </p>
            </div>
          ) : showAssumptions && !isExecuting && processingAgents === 0 && completedAgents === 0 ? (
            /* ── Plan received — awaiting human approval ── */
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-amber-500/10 to-orange-600/10 border border-amber-500/20 flex items-center justify-center mb-6">
                <span className="text-2xl">🛑</span>
              </div>
              <h2 className="text-lg font-semibold text-amber-300 mb-2">
                Human-in-the-Loop Checkpoint
              </h2>
              <p className="text-sm text-slate-400 max-w-md mb-4">
                The strategic audit plan has been generated with <strong className="text-emerald-400">{auditPlan.length} agents</strong>.
                Review the plan and adjust assumptions (WACC, terminal growth) in the Copilot panel before executing.
              </p>
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <div className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
                Awaiting your approval →
              </div>
            </div>
          ) : activeAgents.length === 0 ? (
            /* ── Plan approved but agents haven't started yet ── */
            <div className="flex items-center justify-center h-full">
              <div className="flex items-center gap-3 text-slate-500">
                <svg
                  className="w-5 h-5 animate-spin text-emerald-500/60"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
                <span className="text-sm">
                  Spinning up agent pipeline…
                </span>
              </div>
            </div>
          ) : (
            /* ── Agent Cards Grid ── */
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              {activeAgents.map((agentKey) => (
                <ModularAnalysisCard
                  key={agentKey}
                  agentKey={agentKey}
                  title={AGENT_LABELS[agentKey] ?? agentKey.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                />
              ))}
            </div>
          )}
        </div>

        {/* ── Live Execution Pipeline Sidebar (bottom strip) ── */}
        {hasStarted && auditPlan.length > 0 && (
          <div className="px-6 py-3 border-t border-slate-800/40 bg-slate-900/30">
            <p className="text-[10px] font-semibold tracking-wider uppercase text-slate-500 mb-2">
              Execution Pipeline
            </p>
            <div className="flex flex-wrap gap-2">
              {auditPlan.map((task, idx) => {
                const status = getPipelineStepStatus(task.agent_id);
                return (
                  <div
                    key={task.agent_id}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-mono border transition-all ${
                      status === "success"
                        ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                        : status === "processing"
                        ? "bg-blue-500/10 border-blue-500/30 text-blue-400 animate-pulse"
                        : status === "error"
                        ? "bg-red-500/10 border-red-500/30 text-red-400"
                        : "bg-slate-800/40 border-slate-700/30 text-slate-500"
                    }`}
                  >
                    <span className="font-bold">{idx + 1}.</span>
                    {status === "success" && <span>✅</span>}
                    {status === "processing" && (
                      <svg className="w-2.5 h-2.5 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                    )}
                    {status === "error" && <span>❌</span>}
                    {status === "pending" && <span className="text-slate-600">○</span>}
                    <span className="truncate max-w-[100px]">
                      {AGENT_LABELS[task.agent_id] ?? task.label}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Status Bar ── */}
        <footer className="px-6 py-2 border-t border-slate-800/40 bg-slate-900/30 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div
              className={`w-1.5 h-1.5 rounded-full ${
                isExecuting
                  ? "bg-blue-500 animate-pulse"
                  : isInitiating
                  ? "bg-amber-500 animate-pulse"
                  : hasStarted
                  ? "bg-emerald-500"
                  : "bg-slate-600"
              }`}
            />
            <span className="text-[10px] text-slate-500 font-mono">
              {isInitiating
                ? "Building audit plan…"
                : isExecuting
                ? `Processing — ${totalAgentsCompleted}/${totalAgents} complete`
                : hasStarted
                ? pipelineDuration
                  ? "Pipeline complete"
                  : showAssumptions
                  ? "Awaiting human approval"
                  : "Awaiting execution"
                : "Idle"}
            </span>
          </div>
          <span className="text-[10px] text-slate-600 font-mono">
            Novus FinLLM v1.0
          </span>
        </footer>
      </main>

      {/* ══════════════════════════════════════════════════════════════════════
       *  RIGHT PANE: 30% — Copilot
       * ══════════════════════════════════════════════════════════════════════ */}
      <aside className="w-[30%] flex flex-col bg-slate-900/50">
        {/* ── Copilot Header ── */}
        <div className="px-5 py-3 border-b border-slate-800/50">
          <div className="flex items-center gap-2">
            <div className="w-5 h-5 rounded-md bg-emerald-500/15 flex items-center justify-center">
              <span className="text-[10px]">🤖</span>
            </div>
            <span className="text-xs font-semibold tracking-widest uppercase text-slate-400">
              Research Copilot
            </span>
          </div>
        </div>

        {/* ── Chat Messages ── */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3 scrollbar-thin scrollbar-track-transparent scrollbar-thumb-slate-800">
          {chatMessages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${
                msg.role === "user" ? "justify-end" : "justify-start"
              }`}
            >
              <div
                className={`max-w-[90%] px-3.5 py-2.5 rounded-xl text-xs leading-relaxed ${
                  msg.role === "user"
                    ? "bg-emerald-600/20 text-emerald-300 border border-emerald-500/20"
                    : "bg-slate-800/50 text-slate-400 border border-slate-700/30"
                }`}
              >
                {msg.content}
              </div>
            </div>
          ))}

          {/* ── High-contrast Error Banners ── */}
          {initError && (
            <div className="p-3 bg-red-500/10 border-2 border-red-500/40 rounded-xl text-xs space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="text-red-400 text-sm">🚨</span>
                <span className="font-bold text-red-400 uppercase tracking-wider text-[10px]">
                  Backend Unreachable
                </span>
              </div>
              <p className="text-red-300">{initError}</p>
              <p className="text-red-400/60 text-[10px]">
                Ensure <code className="bg-red-500/10 px-1 rounded">python app.py</code> is running on port 5001.
              </p>
            </div>
          )}
          {executeError && (
            <div className="p-3 bg-red-500/10 border-2 border-red-500/40 rounded-xl text-xs space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="text-red-400 text-sm">⚠️</span>
                <span className="font-bold text-red-400 uppercase tracking-wider text-[10px]">
                  Execution Error
                </span>
              </div>
              <p className="text-red-300">{executeError}</p>
            </div>
          )}

          {/* ── Assumption Widget injected into chat flow ── */}
          {showAssumptions && auditPlan.length > 0 && (
            <div className="my-2">
              <AssumptionWidget />
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        {/* ── Input Area ── */}
        <div className="border-t border-slate-800/50 p-4 space-y-3">
          {!hasStarted ? (
            <>
              {/* Ticker + Query inputs */}
              <div className="flex gap-2">
                <input
                  type="text"
                  value={tickerInput}
                  onChange={(e) =>
                    setTickerInput(e.target.value.toUpperCase())
                  }
                  placeholder="TICKER"
                  className="w-24 bg-slate-800/60 border border-slate-700/40 rounded-lg px-3 py-2 text-xs font-mono text-emerald-400 placeholder:text-slate-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-all uppercase"
                />
                <input
                  type="text"
                  value={queryInput}
                  onChange={(e) => setQueryInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleInitiate()}
                  placeholder="What should we analyze?"
                  className="flex-1 bg-slate-800/60 border border-slate-700/40 rounded-lg px-3 py-2 text-xs text-slate-300 placeholder:text-slate-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-all"
                />
              </div>
              <button
                onClick={handleInitiate}
                disabled={
                  !tickerInput.trim() || !queryInput.trim() || isInitiating
                }
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-xs font-semibold transition-all duration-200 disabled:opacity-40 disabled:cursor-not-allowed bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white shadow-lg shadow-emerald-900/30 active:scale-[0.98]"
              >
                {isInitiating ? (
                  <>
                    <svg
                      className="w-3.5 h-3.5 animate-spin"
                      fill="none"
                      viewBox="0 0 24 24"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                      />
                    </svg>
                    Building Audit Plan…
                  </>
                ) : (
                  "Initiate Research"
                )}
              </button>
            </>
          ) : (
            /* After initiation: quick action chips */
            <div className="flex flex-wrap gap-1.5">
              <button
                onClick={handleReset}
                className="px-3 py-1.5 text-[11px] text-slate-500 bg-slate-800/40 hover:bg-slate-800/70 border border-slate-700/30 rounded-lg transition-all"
              >
                New Analysis
              </button>
              <button className="px-3 py-1.5 text-[11px] text-slate-500 bg-slate-800/40 hover:bg-slate-800/70 border border-slate-700/30 rounded-lg transition-all">
                Export PDF
              </button>
              <button className="px-3 py-1.5 text-[11px] text-slate-500 bg-slate-800/40 hover:bg-slate-800/70 border border-slate-700/30 rounded-lg transition-all">
                Share Report
              </button>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
