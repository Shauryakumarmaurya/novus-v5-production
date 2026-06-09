"use client";

import { useState, useCallback } from "react";
import { useReportStore } from "@/store/useReportStore";

// ─── Props ───────────────────────────────────────────────────────────────────

interface ModularAnalysisCardProps {
  agentKey: string;
  title: string;
}

// ─── Format Utils ────────────────────────────────────────────────────────────

function cleanJsonString(str: string): string {
  let cleaned = str.trim();
  if (cleaned.startsWith("```json")) {
    cleaned = cleaned.replace(/^```json/, "");
  }
  if (cleaned.startsWith("```")) {
    cleaned = cleaned.replace(/^```/, "");
  }
  if (cleaned.endsWith("```")) {
    cleaned = cleaned.replace(/```$/, "");
  }
  return cleaned.trim();
}

function JsonFormatter({ data }: { data: any }) {
  if (data === null) return <span className="text-slate-500">—</span>;
  if (typeof data === "boolean") return <span className={data ? "text-emerald-400" : "text-amber-500"}>{data ? "Yes" : "No"}</span>;
  if (typeof data === "string" || typeof data === "number") return <span>{data}</span>;
  if (Array.isArray(data)) {
    if (data.length === 0) return <span className="text-slate-500">None</span>;
    return (
      <ul className="list-disc list-inside space-y-1 ml-1 text-slate-300">
        {data.map((item, idx) => (
          <li key={idx}><JsonFormatter data={item} /></li>
        ))}
      </ul>
    );
  }
  if (typeof data === "object") {
    return (
      <div className="space-y-2 mt-1 w-full">
        {Object.entries(data).map(([k, v]) => (
          <div key={k} className="bg-slate-800/30 rounded-lg p-2.5 border border-slate-700/40">
            <div className="text-[10px] text-blue-400/80 uppercase tracking-wider font-semibold mb-1.5 flex items-center gap-1.5">
              <span className="w-1 h-3 rounded-full bg-blue-500/50 block"></span>
              {k.replace(/([A-Z])/g, ' $1').replace(/_/g, " ").trim()}
            </div>
            <div className="text-sm text-slate-300 leading-relaxed overflow-x-auto">
              <JsonFormatter data={v} />
            </div>
          </div>
        ))}
      </div>
    );
  }
  return null;
}

function SmartSummary({ text }: { text: string }) {
  if (!text) return null;
  try {
    const cleaned = cleanJsonString(text);
    if ((cleaned.startsWith("{") && cleaned.endsWith("}")) || (cleaned.startsWith("[") && cleaned.endsWith("]"))) {
      const parsed = JSON.parse(cleaned);
      return <JsonFormatter data={parsed} />;
    }
  } catch (e) {
    // Falls back to normal text if JSON parsing fails
  }

  // Fallback normal text
  return <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">{text}</p>;
}

// ─── Skeleton Loader ─────────────────────────────────────────────────────────

function PulseSkeleton() {
  return (
    <div className="space-y-3 animate-pulse p-5">
      <div className="h-3 bg-slate-700/60 rounded-full w-3/4" />
      <div className="h-3 bg-slate-700/40 rounded-full w-full" />
      <div className="h-3 bg-slate-700/40 rounded-full w-5/6" />
      <div className="mt-4 grid grid-cols-2 gap-3">
        <div className="h-16 bg-slate-700/30 rounded-lg" />
        <div className="h-16 bg-slate-700/30 rounded-lg" />
      </div>
      <div className="h-3 bg-slate-700/30 rounded-full w-2/3 mt-3" />
      <div className="h-3 bg-slate-700/20 rounded-full w-1/2" />
    </div>
  );
}

// ─── Verdict Badge ───────────────────────────────────────────────────────────

function VerdictBadge({ verdict }: { verdict: string }) {
  const styles: Record<string, string> = {
    BULLISH: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
    BEARISH: "bg-red-500/15 text-red-400 border-red-500/30",
    NEUTRAL: "bg-slate-500/15 text-slate-400 border-slate-500/30",
    FLAG: "bg-amber-500/15 text-amber-400 border-amber-500/30",
    KILL: "bg-red-600/20 text-red-300 border-red-500/40",
  };

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold tracking-wider uppercase border ${
        styles[verdict] ?? styles.NEUTRAL
      }`}
    >
      {verdict}
    </span>
  );
}

// ─── Confidence Bar ──────────────────────────────────────────────────────────

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 80
      ? "bg-emerald-500"
      : pct >= 60
      ? "bg-amber-500"
      : "bg-red-500";

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-slate-700/50 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ease-out ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[10px] font-mono text-slate-500">{pct}%</span>
    </div>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function ModularAnalysisCard({
  agentKey,
  title,
}: ModularAnalysisCardProps) {
  const card = useReportStore((s) => s.reportCards[agentKey]);
  const challengeAgent = useReportStore((s) => s.challengeAgent);
  const isChallenging = useReportStore((s) => s.isChallenging);

  const [isLocked, setIsLocked] = useState(false);
  const [showChallengeInput, setShowChallengeInput] = useState(false);
  const [challengeFeedback, setChallengeFeedback] = useState("");

  const handleChallenge = useCallback(() => {
    if (!challengeFeedback.trim()) return;
    challengeAgent(agentKey, challengeFeedback.trim());
    setShowChallengeInput(false);
    setChallengeFeedback("");
  }, [agentKey, challengeFeedback, challengeAgent]);

  if (!card) return null;

  const { status, payload, errorMsg } = card;

  return (
    <div
      className={`group relative bg-slate-900/80 border rounded-xl overflow-hidden transition-all duration-300 ${
        status === "processing"
          ? "border-blue-500/40 shadow-lg shadow-blue-500/5"
          : status === "success"
          ? "border-slate-700/50 hover:border-slate-600/60"
          : status === "error"
          ? "border-red-500/40"
          : "border-slate-800/50"
      }`}
    >
      {/* ── Processing glow bar ── */}
      {status === "processing" && (
        <div className="absolute top-0 left-0 right-0 h-0.5">
          <div className="h-full bg-gradient-to-r from-transparent via-blue-500 to-transparent animate-shimmer" />
        </div>
      )}

      {/* ── Header ── */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700/30">
        <div className="flex items-center gap-2.5 min-w-0">
          {/* Status dot */}
          <div
            className={`w-2 h-2 rounded-full flex-shrink-0 ${
              status === "processing"
                ? "bg-blue-500 animate-pulse"
                : status === "success"
                ? "bg-emerald-500"
                : status === "error"
                ? "bg-red-500"
                : "bg-slate-600"
            }`}
          />
          <h3 className="text-sm font-semibold text-slate-200 truncate">
            {title}
          </h3>
          {payload?.verdict && <VerdictBadge verdict={payload.verdict} />}
        </div>

        {/* Action icons */}
        <div className="flex items-center gap-1 flex-shrink-0">
          {/* Lock toggle */}
          <button
            onClick={() => setIsLocked(!isLocked)}
            className={`p-1.5 rounded-md transition-colors ${
              isLocked
                ? "text-amber-400 bg-amber-500/10"
                : "text-slate-500 hover:text-slate-400 hover:bg-slate-800/50"
            }`}
            title={isLocked ? "Unlock finding" : "Lock finding"}
          >
            <span className="text-sm">{isLocked ? "🔒" : "🔓"}</span>
          </button>

          {/* Challenge button */}
          {status === "success" && !isLocked && (
            <button
              onClick={() => setShowChallengeInput(!showChallengeInput)}
              disabled={isChallenging}
              className="p-1.5 rounded-md text-slate-500 hover:text-amber-400 hover:bg-amber-500/10 transition-colors disabled:opacity-40"
              title="Challenge this finding"
            >
              <span className="text-sm">⚡</span>
            </button>
          )}
        </div>
      </div>

      {/* ── Body ── */}
      <div className="relative">
        {/* Locked overlay */}
        {isLocked && status === "success" && (
          <div className="absolute top-0 right-0 m-2 z-10">
            <span className="text-[9px] uppercase tracking-wider font-bold text-amber-500/60 bg-amber-500/5 px-1.5 py-0.5 rounded">
              Locked
            </span>
          </div>
        )}

        {/* ── Loading State ── */}
        {status === "pending" && (
          <div className="flex items-center justify-center py-8">
            <span className="text-xs text-slate-600">Awaiting execution</span>
          </div>
        )}

        {status === "processing" && <PulseSkeleton />}

        {/* ── Error State ── */}
        {status === "error" && (
          <div className="p-5">
            <div className="flex items-start gap-2 p-3 bg-red-500/5 border border-red-500/20 rounded-lg">
              <span className="text-red-400 text-sm mt-0.5">⚠️</span>
              <div>
                <p className="text-xs font-medium text-red-400">
                  Agent Error
                </p>
                <p className="text-[11px] text-red-400/70 mt-0.5">
                  {errorMsg ?? "Unknown error occurred"}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* ── Success: Render Findings ── */}
        {status === "success" && payload && (
          <div className="p-4 space-y-4">
            {/* Summary */}
            <div className="text-slate-300">
              <SmartSummary text={payload.summary} />
            </div>

            {/* Confidence */}
            <ConfidenceBar value={payload.confidence} />

            {/* Metrics Grid */}
            {Object.keys(payload.metrics).length > 0 && (
              <div className="grid grid-cols-2 gap-2">
                {Object.entries(payload.metrics).map(([key, value]) => (
                  <div
                    key={key}
                    className="bg-slate-800/40 border border-slate-700/20 rounded-lg px-3 py-2"
                  >
                    <p className="text-[9px] text-slate-500 uppercase tracking-wider font-medium">
                      {key.replace(/_/g, " ")}
                    </p>
                    <p className="text-sm font-mono text-slate-200 mt-0.5">
                      {value === null
                        ? "—"
                        : typeof value === "boolean"
                        ? value
                          ? "✓"
                          : "✗"
                        : String(value)}
                    </p>
                  </div>
                ))}
              </div>
            )}

            {/* Detail Markdown (rendered as text for now) */}
            {payload.detail_markdown && (
              <details className="group/detail">
                <summary className="text-[11px] text-slate-500 cursor-pointer hover:text-slate-400 transition-colors select-none">
                  View Full Analysis ▾
                </summary>
                <div className="mt-2 p-3 bg-slate-800/30 rounded-lg border border-slate-700/20 text-xs text-slate-400 leading-relaxed whitespace-pre-wrap font-mono max-h-64 overflow-y-auto">
                  {payload.detail_markdown}
                </div>
              </details>
            )}

            {/* Citations */}
            {payload.citations && payload.citations.length > 0 && (
              <div className="pt-2 border-t border-slate-700/20">
                <p className="text-[9px] text-slate-600 uppercase tracking-wider font-medium mb-1.5">
                  Sources
                </p>
                <div className="flex flex-wrap gap-1">
                  {payload.citations.map((cite, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-800/50 border border-slate-700/30 text-[10px] text-slate-400"
                      title={cite.snippet}
                    >
                      📄 {cite.source}
                      {cite.page && (
                        <span className="text-slate-600">p.{cite.page}</span>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Challenge Input Drawer ── */}
      {showChallengeInput && (
        <div className="border-t border-slate-700/30 bg-slate-800/30 p-3">
          <p className="text-[10px] text-amber-400/80 font-medium uppercase tracking-wider mb-2">
            Challenge: {title}
          </p>
          <textarea
            value={challengeFeedback}
            onChange={(e) => setChallengeFeedback(e.target.value)}
            placeholder="Explain why you disagree with this finding…"
            className="w-full bg-slate-900 border border-slate-600/50 rounded-lg px-3 py-2 text-xs text-slate-300 placeholder:text-slate-600 focus:outline-none focus:border-amber-500/40 focus:ring-1 focus:ring-amber-500/20 transition-all resize-none"
            rows={2}
          />
          <div className="flex justify-end gap-2 mt-2">
            <button
              onClick={() => {
                setShowChallengeInput(false);
                setChallengeFeedback("");
              }}
              className="px-3 py-1.5 text-[11px] text-slate-500 hover:text-slate-400 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleChallenge}
              disabled={!challengeFeedback.trim() || isChallenging}
              className="px-3 py-1.5 text-[11px] font-medium bg-amber-500/15 text-amber-400 border border-amber-500/30 rounded-md hover:bg-amber-500/25 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isChallenging ? "Re-analyzing…" : "Submit Challenge"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
