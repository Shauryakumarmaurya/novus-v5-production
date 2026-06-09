import { create } from "zustand";
import { fetchEventSource } from "@microsoft/fetch-event-source";

// ─── API Base ────────────────────────────────────────────────────────────────
const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:5001"
).replace(/\/$/, "");

// ─── Types: Backend Contract ─────────────────────────────────────────────────

/** A single step in the audit execution plan returned by /initiate */
export interface AuditTask {
  agent_id: string; // e.g. "TRIAGE", "FSA_QUANT", "FORENSIC_INVESTIGATOR"
  label: string; // human-readable name
  description: string; // what this agent will do
  order: number; // execution order (0-indexed)
  depends_on: string[]; // agent_ids this task depends on
  estimated_duration_s: number; // rough ETA in seconds
}

/** A baseline assumption surfaced during /initiate for human review */
export interface BaselineAssumption {
  id: string; // unique assumption identifier
  category: "valuation" | "growth" | "risk" | "macro" | "business_model";
  statement: string; // the assumption text
  confidence: number; // 0-1 model confidence
  source: string; // where this was derived from
  editable: boolean; // can the human override this?
  human_override?: string; // optional user-supplied override
}

/** Structured finding returned by an individual agent */
export interface AgentFinding {
  agent_id: string;
  agent_label: string;
  timestamp: string; // ISO-8601
  summary: string; // one-liner conclusion
  verdict: "BULLISH" | "BEARISH" | "NEUTRAL" | "FLAG" | "KILL";
  confidence: number; // 0-1
  metrics: Record<string, string | number | boolean | null>;
  detail_markdown: string; // full markdown analysis body
  citations: Array<{
    source: string;
    page?: number;
    snippet: string;
  }>;
}

// ─── SSE Event Shapes ────────────────────────────────────────────────────────

export interface SSEAgentStart {
  event: "agent_start";
  agent_id: string;
  label: string;
  order: number;
}

export interface SSEAgentComplete {
  event: "agent_complete";
  agent_id: string;
  finding: AgentFinding;
}

export interface SSEAgentError {
  event: "agent_error";
  agent_id: string;
  error: string;
  retriable: boolean;
}

export interface SSEPipelineComplete {
  event: "pipeline_complete";
  total_agents: number;
  duration_s: number;
}

export type SSEEvent =
  | SSEAgentStart
  | SSEAgentComplete
  | SSEAgentError
  | SSEPipelineComplete;

// ─── Report Card ─────────────────────────────────────────────────────────────

export interface ReportCard {
  status: "pending" | "processing" | "success" | "error";
  payload?: AgentFinding;
  errorMsg?: string;
}

// ─── Store Shape ─────────────────────────────────────────────────────────────

interface ReportState {
  // ── Input State ──
  ticker: string;
  query: string;

  // ── Flow Control ──
  hasStarted: boolean;
  showAssumptions: boolean;
  isInitiating: boolean;
  isExecuting: boolean;
  isChallenging: boolean;
  initError: string | null;
  executeError: string | null;
  challengeError: string | null;

  // ── Data ──
  auditPlan: AuditTask[];
  assumptions: BaselineAssumption[];
  reportCards: Record<string, ReportCard>;

  // ── Pipeline Metadata ──
  pipelineDuration: number | null;
  totalAgentsCompleted: number;

  // ── Actions ──
  setTicker: (ticker: string) => void;
  setQuery: (query: string) => void;
  reset: () => void;

  initiateResearch: (ticker: string, query: string) => Promise<void>;
  executePlan: (
    approvedPlan: AuditTask[],
    humanAssumptions: BaselineAssumption[]
  ) => Promise<void>;
  challengeAgent: (
    targetAgent: string,
    feedback: string
  ) => Promise<void>;
}

// ─── Initial State ───────────────────────────────────────────────────────────

const INITIAL_STATE = {
  ticker: "",
  query: "",
  hasStarted: false,
  showAssumptions: false,
  isInitiating: false,
  isExecuting: false,
  isChallenging: false,
  initError: null as string | null,
  executeError: null as string | null,
  challengeError: null as string | null,
  auditPlan: [] as AuditTask[],
  assumptions: [] as BaselineAssumption[],
  reportCards: {} as Record<string, ReportCard>,
  pipelineDuration: null as number | null,
  totalAgentsCompleted: 0,
};

// ─── Store ───────────────────────────────────────────────────────────────────

export const useReportStore = create<ReportState>((set, get) => ({
  ...INITIAL_STATE,

  // ── Simple Setters ──

  setTicker: (ticker) => set({ ticker: ticker.toUpperCase() }),
  setQuery: (query) => set({ query }),

  reset: () => set({ ...INITIAL_STATE }),

  // ──────────────────────────────────────────────────────────────────────────
  // ACTION: initiateResearch
  // POST /api/v1/research/initiate  →  { audit_plan, assumptions }
  // ──────────────────────────────────────────────────────────────────────────

  initiateResearch: async (ticker: string, query: string) => {
    set({
      ticker: ticker.toUpperCase(),
      query,
      hasStarted: true,
      isInitiating: true,
      initError: null,
      auditPlan: [],
      assumptions: [],
      reportCards: {},
      pipelineDuration: null,
      totalAgentsCompleted: 0,
    });

    try {
      const res = await fetch(`${API_BASE}/api/v1/research/initiate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: ticker.toUpperCase(), query }),
      });

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(
          (errBody as Record<string, string>).detail ??
            `Initiate failed (${res.status})`
        );
      }

      const data = (await res.json()) as {
        audit_plan: AuditTask[];
        assumptions: BaselineAssumption[];
      };

      // Pre-seed reportCards with "pending" for every planned agent
      const cards: Record<string, ReportCard> = {};
      for (const task of data.audit_plan) {
        cards[task.agent_id] = { status: "pending" };
      }

      set({
        auditPlan: data.audit_plan,
        assumptions: data.assumptions,
        reportCards: cards,
        showAssumptions: data.assumptions.length > 0,
        isInitiating: false,
      });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Unknown initiation error";
      set({ isInitiating: false, initError: message });
    }
  },

  // ──────────────────────────────────────────────────────────────────────────
  // ACTION: executePlan
  // POST /api/v1/research/execute  →  SSE stream
  // Uses @microsoft/fetch-event-source for robust SSE handling
  // ──────────────────────────────────────────────────────────────────────────

  executePlan: async (
    approvedPlan: AuditTask[],
    humanAssumptions: BaselineAssumption[]
  ) => {
    set({
      isExecuting: true,
      executeError: null,
      showAssumptions: false,
      totalAgentsCompleted: 0,
      pipelineDuration: null,
    });

    // Reset all cards to "pending" based on approved plan
    const freshCards: Record<string, ReportCard> = {};
    for (const task of approvedPlan) {
      freshCards[task.agent_id] = { status: "pending" };
    }
    set({ reportCards: freshCards });

    const abortController = new AbortController();

    try {
      await fetchEventSource(`${API_BASE}/api/v1/research/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker: get().ticker,
          query: get().query,
          approved_plan: approvedPlan,
          assumptions: humanAssumptions,
        }),
        signal: abortController.signal,

        // ── Handle individual SSE messages ──
        onmessage(msg) {
          if (!msg.data) return;

          try {
            const event = JSON.parse(msg.data) as SSEEvent;

            switch (event.event) {
              // ── Agent started processing ──
              case "agent_start": {
                set((state) => ({
                  reportCards: {
                    ...state.reportCards,
                    [event.agent_id]: {
                      status: "processing",
                    },
                  },
                }));
                break;
              }

              // ── Agent completed successfully ──
              case "agent_complete": {
                set((state) => ({
                  totalAgentsCompleted: state.totalAgentsCompleted + 1,
                  reportCards: {
                    ...state.reportCards,
                    [event.agent_id]: {
                      status: "success",
                      payload: event.finding,
                    },
                  },
                }));
                break;
              }

              // ── Agent errored ──
              case "agent_error": {
                set((state) => ({
                  reportCards: {
                    ...state.reportCards,
                    [event.agent_id]: {
                      status: "error",
                      errorMsg: event.error,
                    },
                  },
                }));
                break;
              }

              // ── Entire pipeline finished ──
              case "pipeline_complete": {
                set({
                  isExecuting: false,
                  pipelineDuration: event.duration_s,
                });
                break;
              }
            }
          } catch {
            // Malformed SSE data — swallow silently to keep stream alive
            console.warn("[useReportStore] Unparseable SSE data:", msg.data);
          }
        },

        // ── Connection opened ──
        onopen: async (response) => {
          if (
            response.ok &&
            response.headers
              .get("content-type")
              ?.includes("text/event-stream")
          ) {
            // Connection established successfully
            return;
          }
          throw new Error(
            `SSE connection rejected (${response.status} ${response.statusText})`
          );
        },

        // ── Fatal error — do NOT retry ──
        onerror(err) {
          const message =
            err instanceof Error ? err.message : "SSE stream disconnected";
          set({ isExecuting: false, executeError: message });
          // Throw to prevent automatic retry by fetchEventSource
          throw err;
        },

        // ── Stream closed normally by server ──
        onclose() {
          set((state) => ({
            isExecuting: state.isExecuting ? false : state.isExecuting,
          }));
        },
      });
    } catch (err) {
      // Only set error if not already set by onerror callback
      if (!get().executeError) {
        const message =
          err instanceof Error ? err.message : "Execution stream failed";
        set({ isExecuting: false, executeError: message });
      }
    }
  },

  // ──────────────────────────────────────────────────────────────────────────
  // ACTION: challengeAgent
  // POST /api/v1/research/challenge  →  { updated_findings: AgentFinding[] }
  // Hot-swaps multiple reportCards simultaneously
  // ──────────────────────────────────────────────────────────────────────────

  challengeAgent: async (targetAgent: string, feedback: string) => {
    set({ isChallenging: true, challengeError: null });

    // Mark the challenged agent as "processing" while re-analysis runs
    set((state) => ({
      reportCards: {
        ...state.reportCards,
        [targetAgent]: {
          ...state.reportCards[targetAgent],
          status: "processing",
        },
      },
    }));

    try {
      const res = await fetch(`${API_BASE}/api/v1/research/challenge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker: get().ticker,
          target_agent: targetAgent,
          feedback,
          current_findings: Object.entries(get().reportCards)
            .filter(([, card]) => card.status === "success" && card.payload)
            .map(([, card]) => card.payload),
        }),
      });

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(
          (errBody as Record<string, string>).detail ??
            `Challenge failed (${res.status})`
        );
      }

      const data = (await res.json()) as {
        updated_findings: AgentFinding[];
      };

      // Hot-swap all returned findings into reportCards simultaneously
      set((state) => {
        const nextCards = { ...state.reportCards };
        for (const finding of data.updated_findings) {
          nextCards[finding.agent_id] = {
            status: "success",
            payload: finding,
          };
        }
        return { reportCards: nextCards, isChallenging: false };
      });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Challenge request failed";

      // Revert challenged agent back to its previous success state (or error)
      set((state) => ({
        isChallenging: false,
        challengeError: message,
        reportCards: {
          ...state.reportCards,
          [targetAgent]: {
            ...state.reportCards[targetAgent],
            status: state.reportCards[targetAgent]?.payload
              ? "success"
              : "error",
            errorMsg: message,
          },
        },
      }));
    }
  },
}));
