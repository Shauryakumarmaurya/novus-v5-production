"""
core/memory.py — Institutional-grade persistent memory for Novus Agents

Four tables, all in data/novus_master.db (SQLite w/ WAL):

  agent_mistakes            - append-only record of every correction + unverifiable claim
  data_gaps                 - disclosure gaps deduped by (ticker, agent, metric_category, fiscal_period)
  investigation_patterns    - tool-sequences from high-confidence runs (for future suggestion logic)
  narrative_inconsistencies - cross-period narrative contradictions (the MARQUEE product feature)

Core principle: memory is APPEND-ONLY. Rows are never mutated or deleted.
  - A Q2 fact that is later contradicted by a Q3 fact stays exactly as it was.
  - The contradiction itself is a new row in `narrative_inconsistencies` linking the two.

Execution model for contradiction detection:
  - Synchronous from the orchestrator's POV (so PM Synthesis sees fresh alpha signals in this run)
  - Internally fan-out via ThreadPoolExecutor(max_workers=5) for Tier 2 V3 adjudicator calls
  - Fail-open: adjudication failures log and drop; the algorithm self-heals on the next run
    because Tier 1 always re-scans all prior mistakes, and UNIQUE(mistake_a_id, mistake_b_id)
    prevents duplicate writes when a previously-failed pair eventually succeeds.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
from datetime import datetime, timedelta
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Storage location + schema version
# ═══════════════════════════════════════════════════════════════════════════

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(_REPO_ROOT, "data", "novus_master.db")

SCHEMA_VERSION = 2                # bump when schema changes; migrations are ADDITIVE (no drops)
CRITIC_CONFIDENCE_FLOOR = 0.8     # only inject high-conviction facts into prompts
ADJUDICATOR_TIMEOUT_S = 10.0      # per-pair hard cap for Tier 2 V3 call
ADJUDICATOR_WORKERS = 5           # bounded LLM fan-out width


# ═══════════════════════════════════════════════════════════════════════════
# Metric-category taxonomy
# ═══════════════════════════════════════════════════════════════════════════

# Canonical buckets. Agents/critic are instructed to use these keys, but
# arbitrary lowercase_snake strings are accepted. The rule-based classifier
# below is a fallback for legacy string-only gaps.
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

# Keyword -> bucket mapping (lowercase contains match).
_CLASSIFIER_RULES: list[tuple[tuple[str, ...], str]] = [
    (("ebitda margin", "gross margin", "operating margin", "margin"),    "margins"),
    (("revenue growth", "top-line", "top line"),                         "revenue_growth"),
    (("volume", "tonnage"),                                              "volume_growth"),
    (("roic", "return on invested"),                                     "roic"),
    (("roce", "return on capital employed"),                             "roce"),
    (("ocf", "operating cash", "free cash flow", "fcf", "cash conversion"), "cash_flow"),
    (("working capital", "receivable", "payable", "inventory days"),     "working_capital"),
    (("debt", "leverage", "gearing", "d/e", "net debt"),                 "leverage"),
    (("capex", "capital expenditure", "cwip"),                           "capex"),
    (("guidance", "guided", "guide ", "outlook", "forecast"),            "guidance"),
    (("demand", "offtake", "volume commentary"),                         "demand_commentary"),
    (("pricing power", "price hike", "realisation"),                     "pricing_power"),
    (("governance", "board", "independent director"),                    "governance"),
    (("pledge", "encumbrance"),                                          "promoter_pledge"),
    (("related party", "rpt"),                                           "rpt"),
    (("auditor", "audit qualification", "emphasis of matter"),           "auditor"),
    (("acquisition", "m&a", "merger"),                                   "mna"),
    (("dividend", "buyback", "payout"),                                  "dividend_policy"),
    (("distribution", "reach", "stores", "outlets"),                     "distribution_reach"),
    (("region", "geography", "geographic"),                              "regional_revenue"),
    (("segment", "division"),                                            "segments"),
    (("product mix", "premium mix"),                                     "product_mix"),
    (("moat", "competitive", "market share"),                            "competitive_position"),
    (("sebi", "regulatory", "compliance"),                               "compliance"),
    (("litigation", "lawsuit", "case"),                                  "litigation"),
]


def classify_to_category(text: str) -> str:
    """Rule-based fallback when an agent/critic doesn't emit metric_category.
    Returns one of METRIC_TAXONOMY or 'uncategorized'."""
    if not text:
        return "uncategorized"
    lower = text.lower()
    for keywords, bucket in _CLASSIFIER_RULES:
        for kw in keywords:
            if kw in lower:
                return bucket
    return "uncategorized"


# ═══════════════════════════════════════════════════════════════════════════
# Tier 1 semantic similarity gate
# ═══════════════════════════════════════════════════════════════════════════
#
# We deliberately avoid a keyword polarity lexicon here. Institutional
# management discourse paraphrases too fluidly for rule-based detection
# ("calibrated recovery timeline" vs "structural reset requires recalibration"
# share zero polarity tokens yet represent a clear narrative shift).
#
# Instead: embed every candidate fact pair via the same voyage-finance-2 model
# that powers the RAG stack. A cosine similarity in the SIMILARITY_FLOOR..CEILING
# band means "same subject, non-identical phrasing" — exactly the zone where
# narrative drift lives. We do NOT claim cosine alone detects contradictions;
# the Tier 2 V3 adjudicator owns the CONTRADICTION vs REFINEMENT vs UNRELATED
# decision. Tier 1 is solely a cheap, high-recall topic-drift prefilter.

SIMILARITY_FLOOR = 0.55    # below this: likely different topics, skip
SIMILARITY_CEILING = 0.92  # above this: essentially identical restatement, skip
EMBEDDING_MODEL = "voyage-finance-2"  # reused from rag_engine.py


def _embed_batch(texts: list[str]) -> dict[str, list[float]]:
    """Batch-embed a list of texts via the existing voyage-finance-2 client.

    Design:
      - Lazily imports the voyage client from rag_engine so memory module
        import-time stays decoupled from RAG init (and from the VOYAGE_API_KEY
        env check).
      - Deduplicates input texts before the API call to avoid paying for the
        same fact multiple times across candidate pairs.
      - Single batched call — total network latency is ~150-250ms regardless
        of pair count up to Voyage's batch ceiling.
      - Fail-open: on any import, auth, or network error returns {} so the
        caller degrades to "zero candidates this run" and the self-healing
        next-run re-evaluation takes over.
    """
    if not texts:
        return {}
    unique = sorted({(t or "").strip() for t in texts if t and t.strip()})
    if not unique:
        return {}
    try:
        # Lazy import — same voyage_client instance used by rag_engine, so
        # we share its API key and any future retry/throttle wiring.
        from rag_engine import voyage_client, EMBEDDING_MODEL as RAG_EMBEDDING_MODEL
    except Exception as e:
        logger.warning(f"[MemoryLayer] voyage client import failed: {e}")
        return {}

    model = RAG_EMBEDDING_MODEL or EMBEDDING_MODEL
    try:
        result = voyage_client.embed(
            unique,
            model=model,
            input_type="document",
            truncation=True,
        )
        vectors = result.embeddings
    except Exception as e:
        logger.warning(f"[MemoryLayer] voyage embedding call failed: {e}")
        return {}

    if not vectors or len(vectors) != len(unique):
        logger.warning(
            f"[MemoryLayer] voyage returned {len(vectors) if vectors else 0} "
            f"vectors for {len(unique)} inputs"
        )
        return {}
    return {text: vec for text, vec in zip(unique, vectors)}


def _cosine(a: list[float], b: list[float]) -> float:
    """Plain cosine. No numpy dependency — vectors are ~1024 floats."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))


class DataRetrievalException(Exception):
    """Raised when memory DB is unreachable due to timeout or I/O failure."""
    pass


# ═══════════════════════════════════════════════════════════════════════════
# Memory Layer
# ═══════════════════════════════════════════════════════════════════════════

class MemoryLayer:
    """SQLite-backed institutional memory with WAL concurrency, fiscal-period
    awareness, and native contradiction detection."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # Bounded thread pool for Tier 2 adjudicator calls.
        # Created lazily; shared across all calls to this singleton.
        self._adjudicator_pool: Optional[ThreadPoolExecutor] = None
        self._init_schema()

    # ── Connection helper (WAL mode, no Python-level lock) ────────────────

    def _connect(self) -> sqlite3.Connection:
        import time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
                conn.row_factory = sqlite3.Row
                # WAL + reasonable busy timeout lets multiple readers coexist with a writer
                # without the single-process lock we used to carry.
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                return conn
            except sqlite3.OperationalError as e:
                if attempt == max_retries - 1:
                    raise DataRetrievalException(f"Failed to connect to SQLite memory DB: {e}")
                time.sleep(2 ** attempt)
        raise DataRetrievalException("Failed to connect to SQLite memory DB.")

    def _pool(self) -> ThreadPoolExecutor:
        if self._adjudicator_pool is None:
            self._adjudicator_pool = ThreadPoolExecutor(
                max_workers=ADJUDICATOR_WORKERS,
                thread_name_prefix="MemAdjudicator",
            )
        return self._adjudicator_pool

    # ── Schema init / migration ───────────────────────────────────────────

    # Columns each table must have (name -> ALTER-safe declaration). Used for
    # additive migrations: existing tables get missing columns added, never
    # dropped. Institutional memory must survive schema upgrades and
    # concurrent workers racing through deploys.
    _REQUIRED_COLUMNS = {
        "agent_mistakes": {
            "fiscal_period": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "metric_category": "TEXT NOT NULL DEFAULT 'uncategorized'",
            "citations_json": "TEXT",
            "source_citation": "TEXT",
            "critic_confidence": "REAL DEFAULT 0.0",
        },
        "data_gaps": {
            "metric_category": "TEXT NOT NULL DEFAULT 'uncategorized'",
            "fiscal_period": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "occurrence_count": "INTEGER DEFAULT 1",
        },
        "investigation_patterns": {
            "fiscal_period": "TEXT",
        },
        "narrative_inconsistencies": {
            "inconsistency_type": "TEXT",
            "severity": "TEXT",
            "adjudicator_rationale": "TEXT",
        },
    }

    @staticmethod
    def _ensure_columns(conn, table: str, required: dict) -> None:
        """Additively migrate an existing table: add any missing columns."""
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            return  # table doesn't exist yet — CREATE TABLE below handles it
        for col, decl in required.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                logger.info(f"[MemoryLayer] Migration: added {table}.{col}")

    def _init_schema(self):
        with self._connect() as conn:
            cur = conn.execute("PRAGMA user_version")
            current_version = cur.fetchone()[0]

            if current_version < SCHEMA_VERSION:
                logger.info(
                    f"[MemoryLayer] Schema upgrade: {current_version} -> {SCHEMA_VERSION} "
                    "(additive — existing rows preserved)."
                )
                for table, required in self._REQUIRED_COLUMNS.items():
                    self._ensure_columns(conn, table, required)

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_mistakes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    fiscal_period TEXT NOT NULL,
                    metric_category TEXT NOT NULL,
                    original_claim TEXT,
                    verified_fact TEXT,
                    correction_type TEXT,
                    citations_json TEXT,
                    source_citation TEXT,
                    run_date TEXT NOT NULL,
                    critic_confidence REAL DEFAULT 0.0
                );
                CREATE INDEX IF NOT EXISTS idx_mistakes_period
                    ON agent_mistakes(ticker, fiscal_period);
                CREATE INDEX IF NOT EXISTS idx_mistakes_drift
                    ON agent_mistakes(ticker, metric_category, fiscal_period);
                CREATE INDEX IF NOT EXISTS idx_mistakes_ticker_agent
                    ON agent_mistakes(ticker, agent_name);

                CREATE TABLE IF NOT EXISTS data_gaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    metric_category TEXT NOT NULL,
                    fiscal_period TEXT NOT NULL,
                    gap_description TEXT NOT NULL,
                    occurrence_count INTEGER DEFAULT 1,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    UNIQUE(ticker, agent_name, metric_category, fiscal_period)
                );
                CREATE INDEX IF NOT EXISTS idx_gaps_drift
                    ON data_gaps(ticker, metric_category);
                CREATE INDEX IF NOT EXISTS idx_gaps_period
                    ON data_gaps(ticker, fiscal_period);

                CREATE TABLE IF NOT EXISTS investigation_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    fiscal_period TEXT,
                    tool_sequence TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    run_date TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_patterns_agent
                    ON investigation_patterns(agent_name);

                CREATE TABLE IF NOT EXISTS narrative_inconsistencies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    metric_category TEXT NOT NULL,
                    mistake_a_id INTEGER NOT NULL,
                    mistake_b_id INTEGER NOT NULL,
                    fiscal_period_a TEXT NOT NULL,
                    fiscal_period_b TEXT NOT NULL,
                    inconsistency_type TEXT,
                    severity TEXT,
                    adjudicator_rationale TEXT,
                    detected_at TEXT NOT NULL,
                    FOREIGN KEY(mistake_a_id) REFERENCES agent_mistakes(id),
                    FOREIGN KEY(mistake_b_id) REFERENCES agent_mistakes(id),
                    UNIQUE(mistake_a_id, mistake_b_id)
                );
                CREATE INDEX IF NOT EXISTS idx_incon_ticker
                    ON narrative_inconsistencies(ticker, metric_category);
                """
            )
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    # ═══════════════════════════════════════════════════════════════════════
    # WRITE PATH
    # ═══════════════════════════════════════════════════════════════════════

    def store_corrections(
        self,
        critic_findings: dict,
        ticker: str,
        fiscal_period: str,
    ) -> dict:
        """
        Persist every correction and unverifiable claim from the Critic's output,
        then run contradiction detection against prior memory.

        Args:
            critic_findings: CriticAgentV3 output. Expected keys:
                corrections[], unverifiable_claims[], verification_status
            ticker: uppercased ticker
            fiscal_period: e.g. "Q3_FY26" or "FY25" (REQUIRED)

        Returns:
            {"mistakes_written": N, "gaps_upserted": N, "inconsistencies_found": N}
        """
        result = {"mistakes_written": 0, "gaps_upserted": 0, "inconsistencies_found": 0}
        if not critic_findings or not isinstance(critic_findings, dict):
            return result

        ticker = (ticker or "").upper().strip()
        fiscal_period = (fiscal_period or "UNKNOWN").strip()
        if not ticker:
            return result

        now = datetime.utcnow().isoformat()
        corrections = critic_findings.get("corrections", []) or []
        unverifiable = critic_findings.get("unverifiable_claims", []) or []
        if not corrections and not unverifiable:
            return result

        new_mistake_ids: list[int] = []

        try:
            with self._connect() as conn:
                conn.execute("BEGIN")
                # ── Concrete corrections ──
                for c in corrections:
                    if not isinstance(c, dict):
                        continue
                    agent_name = (c.get("agent_name") or "").strip()
                    original = str(c.get("original_claim") or "").strip()
                    verified = str(c.get("verified_fact") or "").strip()
                    if not agent_name or not original or not verified:
                        continue
                    category = (c.get("metric_category") or "").strip() \
                        or classify_to_category(f"{original} {verified}")
                    period = (c.get("fiscal_period") or fiscal_period).strip()
                    citations = c.get("citations") or []
                    citations_json = json.dumps(citations, default=str) if citations else None
                    source_citation = c.get("source_citation") or self._flatten_citations(citations)

                    cur = conn.execute(
                        """INSERT INTO agent_mistakes
                           (ticker, agent_name, fiscal_period, metric_category,
                            original_claim, verified_fact, correction_type,
                            citations_json, source_citation,
                            run_date, critic_confidence)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            ticker, agent_name, period, category,
                            original[:1500], verified[:1500],
                            str(c.get("action") or "CORRECTED")[:50],
                            citations_json,
                            str(source_citation or "")[:500],
                            now,
                            float(c.get("confidence", 0.8)),
                        ),
                    )
                    new_mistake_ids.append(cur.lastrowid)
                    result["mistakes_written"] += 1

                # ── Unverifiable claims: write to mistakes + dedup into data_gaps ──
                for u in unverifiable:
                    if not isinstance(u, dict):
                        continue
                    agent_name = (u.get("agent_name") or "").strip()
                    claim = str(u.get("claim") or "").strip()
                    reason = str(u.get("reason") or "")
                    if not agent_name or not claim:
                        continue
                    category = (u.get("metric_category") or "").strip() \
                        or classify_to_category(claim)
                    period = (u.get("fiscal_period") or fiscal_period).strip()
                    citations = u.get("citations") or []
                    citations_json = json.dumps(citations, default=str) if citations else None

                    cur = conn.execute(
                        """INSERT INTO agent_mistakes
                           (ticker, agent_name, fiscal_period, metric_category,
                            original_claim, verified_fact, correction_type,
                            citations_json, source_citation,
                            run_date, critic_confidence)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            ticker, agent_name, period, category,
                            claim[:1500],
                            f"UNVERIFIABLE: {reason}"[:1500],
                            str(u.get("action") or "FLAGGED_AS_DATA_GAP")[:50],
                            citations_json,
                            "",
                            now,
                            0.5,
                        ),
                    )
                    new_mistake_ids.append(cur.lastrowid)
                    result["mistakes_written"] += 1

                    # Upsert into data_gaps keyed by (ticker, agent, category, period)
                    conn.execute(
                        """INSERT INTO data_gaps
                           (ticker, agent_name, metric_category, fiscal_period,
                            gap_description, occurrence_count, first_seen, last_seen)
                           VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                           ON CONFLICT(ticker, agent_name, metric_category, fiscal_period)
                           DO UPDATE SET
                               occurrence_count = occurrence_count + 1,
                               last_seen = excluded.last_seen""",
                        (ticker, agent_name, category, period, claim[:500], now, now),
                    )
                    result["gaps_upserted"] += 1

                conn.execute("COMMIT")
        except Exception as e:
            logger.warning(f"[MemoryLayer] store_corrections write failed: {e}")
            return result

        logger.info(
            f"[MemoryLayer] Stored {result['mistakes_written']} mistakes + "
            f"{result['gaps_upserted']} gaps for {ticker} {fiscal_period}"
        )

        # ── Fire contradiction detection for the new mistake rows ──
        # This is synchronous-from-caller but parallelized internally via the
        # bounded thread pool, so PM Synthesis in the same orchestrator run
        # sees any contradictions we detect.
        if new_mistake_ids:
            try:
                found = self.detect_narrative_contradictions(new_mistake_ids)
                result["inconsistencies_found"] = found
            except Exception as e:
                logger.warning(f"[MemoryLayer] contradiction detection failed: {e}")

        return result

    def store_agent_data_gaps(
        self,
        agent_name: str,
        ticker: str,
        gaps: list,
        fiscal_period: str,
    ) -> int:
        """Persist gaps reported directly by an agent.

        Args:
            gaps: list[str] (legacy) OR list[dict] with keys
                  {metric_category?, description, fiscal_period?}
        """
        if not gaps:
            return 0
        ticker = (ticker or "").upper().strip()
        fiscal_period = (fiscal_period or "UNKNOWN").strip()
        now = datetime.utcnow().isoformat()
        count = 0

        try:
            with self._connect() as conn:
                conn.execute("BEGIN")
                for g in gaps:
                    if isinstance(g, dict):
                        desc = str(g.get("description") or "").strip()
                        if not desc:
                            continue
                        category = (g.get("metric_category") or "").strip() \
                            or classify_to_category(desc)
                        period = (g.get("fiscal_period") or fiscal_period).strip()
                    else:
                        desc = str(g or "").strip()
                        if not desc:
                            continue
                        category = classify_to_category(desc)
                        period = fiscal_period

                    conn.execute(
                        """INSERT INTO data_gaps
                           (ticker, agent_name, metric_category, fiscal_period,
                            gap_description, occurrence_count, first_seen, last_seen)
                           VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                           ON CONFLICT(ticker, agent_name, metric_category, fiscal_period)
                           DO UPDATE SET
                               occurrence_count = occurrence_count + 1,
                               last_seen = excluded.last_seen""",
                        (ticker, agent_name, category, period, desc[:500], now, now),
                    )
                    count += 1
                conn.execute("COMMIT")
        except Exception as e:
            logger.warning(f"[MemoryLayer] store_agent_data_gaps failed: {e}")
            return 0
        return count

    def store_investigation_pattern(
        self,
        agent_name: str,
        ticker: str,
        tool_sequence: list[str],
        confidence: float,
        fiscal_period: Optional[str] = None,
    ) -> bool:
        if confidence < 0.7 or not tool_sequence:
            return False
        ticker = (ticker or "").upper().strip()
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO investigation_patterns
                       (agent_name, ticker, fiscal_period, tool_sequence,
                        confidence, run_date)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        agent_name,
                        ticker,
                        (fiscal_period or "").strip() or None,
                        json.dumps(tool_sequence[:20]),
                        float(confidence),
                        now,
                    ),
                )
            return True
        except Exception as e:
            logger.warning(f"[MemoryLayer] store_investigation_pattern failed: {e}")
            return False

    # ═══════════════════════════════════════════════════════════════════════
    # CONTRADICTION DETECTION (the alpha engine)
    # ═══════════════════════════════════════════════════════════════════════

    def detect_narrative_contradictions(self, new_mistake_ids: list[int]) -> int:
        """For each new mistake row, scan prior facts in the same
        (ticker, metric_category). Tier 1 semantic similarity gate
        (voyage-finance-2 cosine in [SIMILARITY_FLOOR, SIMILARITY_CEILING])
        -> Tier 2 V3 adjudicator fan-out -> persist only CONTRADICTION rows.

        Returns the number of new inconsistency rows written.
        """
        if not new_mistake_ids:
            return 0

        # Tier 1 — collect candidate pairs via SQL + polarity check
        candidates = self._tier1_candidate_pairs(new_mistake_ids)
        if not candidates:
            return 0

        logger.info(
            f"[MemoryLayer] Tier 1 flagged {len(candidates)} candidate pair(s). "
            "Dispatching Tier 2 adjudicator..."
        )

        # Tier 2 — parallel V3 adjudication, bounded
        pool = self._pool()
        futures = {pool.submit(self._tier2_adjudicate, pair): pair for pair in candidates}
        verdicts: list[tuple[dict, dict]] = []  # (pair, verdict)
        for fut, pair in futures.items():
            try:
                verdict = fut.result(timeout=ADJUDICATOR_TIMEOUT_S)
                if verdict is not None:
                    verdicts.append((pair, verdict))
            except _FuturesTimeout:
                logger.warning(f"[MemoryLayer] Tier 2 timeout for pair {pair.get('a_id')}->{pair.get('b_id')}")
            except Exception as e:
                logger.warning(f"[MemoryLayer] Tier 2 call failed: {e}")

        if not verdicts:
            return 0

        # Persist only CONTRADICTION labels, batched
        written = 0
        now = datetime.utcnow().isoformat()
        try:
            with self._connect() as conn:
                conn.execute("BEGIN")
                for pair, verdict in verdicts:
                    if verdict.get("label") != "CONTRADICTION":
                        continue
                    try:
                        conn.execute(
                            """INSERT INTO narrative_inconsistencies
                               (ticker, metric_category,
                                mistake_a_id, mistake_b_id,
                                fiscal_period_a, fiscal_period_b,
                                inconsistency_type, severity,
                                adjudicator_rationale, detected_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                pair["ticker"],
                                pair["metric_category"],
                                pair["a_id"], pair["b_id"],
                                pair["period_a"], pair["period_b"],
                                verdict.get("inconsistency_type") or "REASON_DRIFT",
                                verdict.get("severity", "MEDIUM"),
                                str(verdict.get("rationale", ""))[:600],
                                now,
                            ),
                        )
                        written += 1
                    except sqlite3.IntegrityError:
                        # UNIQUE(mistake_a_id, mistake_b_id) already satisfied by a prior run
                        pass
                conn.execute("COMMIT")
        except Exception as e:
            logger.warning(f"[MemoryLayer] contradiction persist failed: {e}")
            return 0

        if written:
            logger.info(f"[MemoryLayer] Persisted {written} narrative_inconsistencies row(s).")
        return written

    def _tier1_candidate_pairs(self, new_mistake_ids: list[int]) -> list[dict]:
        """Find (new_mistake, prior_mistake) pairs in the same (ticker, category)
        whose verified_facts fall in the semantic-drift band.

        Pipeline:
          1. SQL: pull prior, not-yet-linked mistakes for each new one.
          2. Dedup all distinct fact strings across all raw pairs.
          3. ONE batched voyage-finance-2 embedding call.
          4. Pairwise cosine; retain pairs with SIMILARITY_FLOOR <= cos <= CEILING.
             Attach the similarity score so the Tier 2 adjudicator can calibrate.

        Any failure in the embedding call causes a fail-open return of [] —
        the next run will re-evaluate these pairs thanks to the UNIQUE
        constraint on narrative_inconsistencies guarding against duplicates.
        """
        if not new_mistake_ids:
            return []
        placeholders = ",".join("?" * len(new_mistake_ids))

        # ── Step 1: SQL pull — gather all raw candidate pairs ──
        raw_pairs: list[dict] = []
        with self._connect() as conn:
            new_rows = conn.execute(
                f"""SELECT id, ticker, agent_name, fiscal_period,
                           metric_category, verified_fact, original_claim
                    FROM agent_mistakes
                    WHERE id IN ({placeholders})""",
                new_mistake_ids,
            ).fetchall()

            for new in new_rows:
                if (new["verified_fact"] or "").startswith("UNVERIFIABLE"):
                    continue
                prior_rows = conn.execute(
                    """SELECT id, fiscal_period, verified_fact, original_claim
                       FROM agent_mistakes
                       WHERE ticker = ?
                         AND metric_category = ?
                         AND id != ?
                         AND fiscal_period != ?
                         AND id NOT IN (
                             SELECT mistake_a_id FROM narrative_inconsistencies WHERE mistake_b_id = ?
                             UNION
                             SELECT mistake_b_id FROM narrative_inconsistencies WHERE mistake_a_id = ?
                         )
                         AND (verified_fact IS NULL OR verified_fact NOT LIKE 'UNVERIFIABLE%')""",
                    (
                        new["ticker"], new["metric_category"],
                        new["id"], new["fiscal_period"],
                        new["id"], new["id"],
                    ),
                ).fetchall()

                for prior in prior_rows:
                    prior_fact = (prior["verified_fact"] or "").strip()
                    new_fact = (new["verified_fact"] or "").strip()
                    if not prior_fact or not new_fact:
                        continue
                    # Earlier period goes in slot A by convention
                    if prior["fiscal_period"] <= new["fiscal_period"]:
                        a_id, b_id = prior["id"], new["id"]
                        period_a, period_b = prior["fiscal_period"], new["fiscal_period"]
                        fact_a, fact_b = prior_fact, new_fact
                    else:
                        a_id, b_id = new["id"], prior["id"]
                        period_a, period_b = new["fiscal_period"], prior["fiscal_period"]
                        fact_a, fact_b = new_fact, prior_fact
                    raw_pairs.append({
                        "ticker": new["ticker"],
                        "metric_category": new["metric_category"],
                        "a_id": a_id, "b_id": b_id,
                        "period_a": period_a, "period_b": period_b,
                        "fact_a": fact_a, "fact_b": fact_b,
                    })

        if not raw_pairs:
            return []

        # ── Step 2: dedup fact strings across all raw pairs ──
        texts_to_embed: set[str] = set()
        for p in raw_pairs:
            texts_to_embed.add(p["fact_a"])
            texts_to_embed.add(p["fact_b"])

        # ── Step 3: single batched voyage-finance-2 embedding call ──
        vectors = _embed_batch(list(texts_to_embed))
        if not vectors:
            logger.warning(
                f"[MemoryLayer] Tier 1 degraded: embedding failed for "
                f"{len(raw_pairs)} raw pair(s). Self-heal on next run."
            )
            return []

        # ── Step 4: cosine-filter by semantic-drift band ──
        filtered: list[dict] = []
        for p in raw_pairs:
            va = vectors.get(p["fact_a"])
            vb = vectors.get(p["fact_b"])
            if not va or not vb:
                continue
            sim = _cosine(va, vb)
            if SIMILARITY_FLOOR <= sim <= SIMILARITY_CEILING:
                p["similarity"] = round(sim, 4)
                filtered.append(p)

        logger.info(
            f"[MemoryLayer] Tier 1 semantic filter: {len(raw_pairs)} raw pairs -> "
            f"{len(filtered)} in [{SIMILARITY_FLOOR}, {SIMILARITY_CEILING}] band."
        )
        return filtered

    def _tier2_adjudicate(self, pair: dict) -> Optional[dict]:
        """One V3 call. Returns a parsed dict or None on any failure."""
        # Import at call-site so a misconfigured LLM client never blocks memory imports.
        try:
            from core.llm_client import get_llm_client
        except Exception as e:
            logger.warning(f"[MemoryLayer] llm_client import failed: {e}")
            return None

        similarity = pair.get("similarity")
        sim_hint = ""
        if isinstance(similarity, (int, float)):
            sim_hint = (
                f"\n\nSemantic similarity (voyage-finance-2 cosine): {similarity:.3f}. "
                "As calibration: 0.85+ typically indicates REFINEMENT or restatement; "
                "0.60-0.80 indicates same subject with potentially divergent stance. "
                "Use this as one input among many — your own semantic judgment is primary."
            )

        prompt = (
            f"Two independently verified facts were recorded for the same metric "
            f"category '{pair['metric_category']}' for ticker {pair['ticker']}.\n\n"
            f"Fact A (fiscal_period={pair['period_a']}): \"{pair['fact_a']}\"\n"
            f"Fact B (fiscal_period={pair['period_b']}): \"{pair['fact_b']}\"\n\n"
            "Both facts were verified against source documents at the time of their run. "
            "Classify the relationship:\n"
            "- CONTRADICTION: management's story reversed or the stated reason changed materially.\n"
            "- REFINEMENT: magnitude or detail evolved but the underlying narrative is consistent.\n"
            "- UNRELATED: the facts address different subjects despite sharing a category."
            f"{sim_hint}\n\n"
            "You own the inconsistency_type classification. Pick the single best label from:\n"
            "- REASON_DRIFT: same outcome, different stated cause (e.g. 'supply chain' -> 'demand')\n"
            "- POLARITY_REVERSAL: 'no material impact' -> 'material impact', or similar flip\n"
            "- DENIAL_TO_ADMISSION: 'we have no exposure' -> 'our exposure is X'\n\n"
            "Output strict JSON only:\n"
            "{\"label\": \"CONTRADICTION|REFINEMENT|UNRELATED\", "
            "\"inconsistency_type\": \"REASON_DRIFT|POLARITY_REVERSAL|DENIAL_TO_ADMISSION|null\", "
            "\"severity\": \"LOW|MEDIUM|HIGH\", "
            "\"rationale\": \"one concise sentence\"}"
        )
        system = (
            "You are a forensic research auditor. You distinguish between a company "
            "refining its narrative (normal) and reversing it (an alpha signal). Only "
            "label CONTRADICTION when management has materially changed their story."
        )
        try:
            client = get_llm_client(use_r1=False)  # V3 is cheap + structured
            raw = client.call_simple(system, prompt) or ""
        except Exception as e:
            logger.warning(f"[MemoryLayer] adjudicator V3 call failed: {e}")
            return None

        # Extract JSON from the response (handles bare JSON or fenced blocks)
        text = raw.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].rsplit("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].rsplit("```", 1)[0]
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            text = m.group(0)
        try:
            parsed = json.loads(text.strip())
        except json.JSONDecodeError:
            logger.warning(f"[MemoryLayer] adjudicator returned non-JSON: {raw[:200]}")
            return None
        if not isinstance(parsed, dict) or "label" not in parsed:
            return None
        return parsed

    # ═══════════════════════════════════════════════════════════════════════
    # READ PATH (prompt injection)
    # ═══════════════════════════════════════════════════════════════════════

    def load_relevant_memories(
        self,
        agent_name: str,
        ticker: str,
        target_fiscal_period: str,
        max_mistakes_same_period: int = 4,
        max_mistakes_lookback: int = 3,
        max_gaps: int = 5,
        max_inconsistencies: int = 5,
        lookback_days: int = 180,
    ) -> str:
        """Build the memory injection block for a specific agent + ticker +
        target fiscal period.

        Returns '' when there's nothing to inject — callers can safely concat.
        """
        ticker = (ticker or "").upper().strip()
        target_fiscal_period = (target_fiscal_period or "").strip()
        if not ticker or not agent_name:
            return ""

        cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
        blocks: list[str] = []

        try:
            with self._connect() as conn:
                if not target_fiscal_period:
                    # Default to most recent period
                    row = conn.execute(
                        "SELECT fiscal_period FROM agent_mistakes WHERE ticker = ? ORDER BY run_date DESC, id DESC LIMIT 1",
                        (ticker,)
                    ).fetchone()
                    if row:
                        target_fiscal_period = row["fiscal_period"]
                    else:
                        return "WARNING: No target_fiscal_period provided and no prior memory exists."

                # ── Block 2: Same-period high-conviction mistakes (Tier 1) ──
                tier1_rows = conn.execute(
                    """SELECT id, original_claim, verified_fact, correction_type,
                              source_citation, run_date, metric_category, fiscal_period
                       FROM agent_mistakes
                       WHERE ticker = ? AND agent_name = ?
                         AND fiscal_period = ?
                         AND critic_confidence >= ?
                       ORDER BY run_date DESC, id DESC
                       LIMIT ?""",
                    (ticker, agent_name, target_fiscal_period,
                     CRITIC_CONFIDENCE_FLOOR, max_mistakes_same_period),
                ).fetchall()

                if tier1_rows:
                    lines = [f"## MEMORY: PAST MISTAKES ON {ticker} (high-conviction only)"]
                    for r in tier1_rows:
                        lines.append(self._render_mistake_line(r, tag="[SAME PERIOD]"))
                    blocks.append("\n\n".join(lines))

                # ── Block 4: Recurring data gaps — strictly for target_fiscal_period ──
                gap_rows = conn.execute(
                    """SELECT metric_category,
                              occurrence_count AS total_occurrences,
                              1 AS periods_count,
                              last_seen,
                              fiscal_period AS periods,
                              gap_description AS sample_desc
                       FROM data_gaps
                       WHERE ticker = ? AND agent_name = ? AND fiscal_period = ?
                       ORDER BY total_occurrences DESC, id ASC
                       LIMIT ?""",
                    (ticker, agent_name, target_fiscal_period, max_gaps),
                ).fetchall()

                if gap_rows:
                    lines = [f"## MEMORY: KNOWN DATA GAPS FOR {ticker} ({target_fiscal_period})"]
                    for g in gap_rows:
                        lines.append(
                            f"[RECURRING GAP - {g['metric_category']}, "
                            f"{g['periods_count']} period(s), {g['total_occurrences']}x seen] "
                            f"Sample: \"{(g['sample_desc'] or '')[:220]}\" "
                            f"across {g['periods'] or 'multiple periods'}.\n"
                            f"  -> Do NOT fabricate. State 'Data not available in filed documents' "
                            f"if this metric is needed."
                        )
                    blocks.append("\n".join(lines))

                # ── Block 5: Cross-ticker volume nudge (only when material) ──
                pattern_rows = conn.execute(
                    """SELECT correction_type, COUNT(*) AS n
                       FROM agent_mistakes
                       WHERE agent_name = ? AND run_date >= ?
                         AND critic_confidence >= ?
                       GROUP BY correction_type
                       ORDER BY n DESC, correction_type ASC""",
                    (agent_name, cutoff, CRITIC_CONFIDENCE_FLOOR),
                ).fetchall()
                total = sum(r["n"] for r in pattern_rows)
                if total >= 5:
                    breakdown = ", ".join(
                        f"{r['n']}x {r['correction_type']}" for r in pattern_rows
                    )
                    blocks.append(
                        f"## AGENT PATTERN (cross-ticker, last {lookback_days}d)\n"
                        f"{agent_name} has been corrected {total} times recently "
                        f"({breakdown}). Verify every hard number with a tool call "
                        f"before including it in your findings."
                    )
        except Exception as e:
            logger.warning(f"[MemoryLayer] load_relevant_memories failed: {e}")
            return ""

        if not blocks:
            return ""

        header = (
            "\n\n## LEARNED MEMORY FROM PAST RUNS (DO NOT REPEAT THESE MISTAKES)\n"
            "The following is a record of prior verified errors, recurring data gaps, "
            "and cross-period narrative inconsistencies for this company. Use tools to "
            "avoid repeating any mistake below. Treat [ALPHA SIGNAL] blocks as first-class findings."
        )
        return header + "\n\n" + "\n\n".join(blocks)

    def _render_mistake_line(self, r: sqlite3.Row, tag: str) -> str:
        date = (r["run_date"] or "")[:10]
        ctype = r["correction_type"] or "CORRECTED"
        original = (r["original_claim"] or "")[:250]
        verified = (r["verified_fact"] or "")[:250]
        citation = r["source_citation"] or ""
        cat = r["metric_category"] or "uncategorized"
        return (
            f"{tag} [{ctype} | {cat}] You previously claimed: \"{original}\"\n"
            f"  -> VERIFIED FACT: {verified}\n"
            f"  -> LESSON: Verify this category with tools (get_metric / compute_ratio / search_document). "
            f"Do not report from memory."
            + (f"\n  -> Source: {citation}" if citation else "")
            + f"\n  (Run: {date})"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # PM PRODUCT FEATURES
    # ═══════════════════════════════════════════════════════════════════════

    def get_audit_trail(self, mistake_id: int) -> Optional[dict]:
        """Hover-to-Verify provenance. Full row + deserialized citations."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """SELECT * FROM agent_mistakes WHERE id = ?""",
                    (int(mistake_id),),
                ).fetchone()
        except Exception as e:
            logger.warning(f"[MemoryLayer] get_audit_trail failed: {e}")
            return None
        if not row:
            return None
        data = dict(row)
        data["citations"] = self._load_citations(data.pop("citations_json", None))
        return data

    def get_thesis_drift(
        self,
        ticker: str,
        metric_category: str,
        max_periods: int = 8,
    ) -> list[dict]:
        """Time-ordered verified claims for one metric category — powers
        the 'How Has The Story Changed?' panel."""
        ticker = (ticker or "").upper().strip()
        if not ticker or not metric_category:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT id, fiscal_period, verified_fact, original_claim,
                              source_citation, citations_json, run_date, critic_confidence,
                              agent_name
                       FROM agent_mistakes
                       WHERE ticker = ? AND metric_category = ?
                         AND (verified_fact IS NULL OR verified_fact NOT LIKE 'UNVERIFIABLE%')
                       ORDER BY fiscal_period ASC, run_date ASC, id ASC
                       LIMIT ?""",
                    (ticker, metric_category, max_periods),
                ).fetchall()
        except Exception as e:
            logger.warning(f"[MemoryLayer] get_thesis_drift failed: {e}")
            return []

        out: list[dict] = []
        seen_periods = set()
        for r in rows:
            if r["fiscal_period"] in seen_periods:
                continue
            seen_periods.add(r["fiscal_period"])
            d = dict(r)
            d["citations"] = self._load_citations(d.pop("citations_json", None))
            out.append(d)
        return out

    def get_negative_space_report(
        self,
        ticker: str,
        min_periods: int = 2,
    ) -> list[dict]:
        """'Silent Signals' — metrics the company has not disclosed for >=N periods."""
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT metric_category,
                              agent_name,
                              COUNT(DISTINCT fiscal_period) AS periods_count,
                              GROUP_CONCAT(DISTINCT fiscal_period) AS periods,
                              SUM(occurrence_count) AS total_occurrences,
                              MAX(last_seen) AS last_seen,
                              (SELECT gap_description FROM data_gaps g2
                                 WHERE g2.ticker = data_gaps.ticker
                                   AND g2.metric_category = data_gaps.metric_category
                                 ORDER BY g2.last_seen DESC, g2.id DESC LIMIT 1) AS sample_description
                       FROM data_gaps
                       WHERE ticker = ?
                       GROUP BY metric_category
                       HAVING periods_count >= ?
                       ORDER BY periods_count DESC, total_occurrences DESC, metric_category ASC""",
                    (ticker, int(min_periods)),
                ).fetchall()
        except Exception as e:
            logger.warning(f"[MemoryLayer] get_negative_space_report failed: {e}")
            return []
        return [dict(r) for r in rows]

    def get_management_inconsistencies(self, ticker: str) -> list[dict]:
        """Marquee feature: every detected narrative inconsistency for a ticker,
        joined with both underlying mistakes including their citations."""
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT ni.*,
                              ma.verified_fact AS fact_a_text,
                              ma.citations_json AS fact_a_citations,
                              ma.agent_name AS fact_a_agent,
                              mb.verified_fact AS fact_b_text,
                              mb.citations_json AS fact_b_citations,
                              mb.agent_name AS fact_b_agent
                       FROM narrative_inconsistencies ni
                       LEFT JOIN agent_mistakes ma ON ma.id = ni.mistake_a_id
                       LEFT JOIN agent_mistakes mb ON mb.id = ni.mistake_b_id
                       WHERE ni.ticker = ?
                       ORDER BY ni.detected_at DESC, ni.id DESC""",
                    (ticker,),
                ).fetchall()
        except Exception as e:
            logger.warning(f"[MemoryLayer] get_management_inconsistencies failed: {e}")
            return []

        out: list[dict] = []
        for r in rows:
            d = dict(r)
            out.append({
                "id": d["id"],
                "ticker": d["ticker"],
                "metric_category": d["metric_category"],
                "inconsistency_type": d["inconsistency_type"],
                "severity": d["severity"],
                "rationale": d["adjudicator_rationale"],
                "detected_at": d["detected_at"],
                "fact_a": {
                    "mistake_id": d["mistake_a_id"],
                    "fiscal_period": d["fiscal_period_a"],
                    "agent_name": d["fact_a_agent"],
                    "verified_fact": d["fact_a_text"],
                    "citations": self._load_citations(d["fact_a_citations"]),
                },
                "fact_b": {
                    "mistake_id": d["mistake_b_id"],
                    "fiscal_period": d["fiscal_period_b"],
                    "agent_name": d["fact_b_agent"],
                    "verified_fact": d["fact_b_text"],
                    "citations": self._load_citations(d["fact_b_citations"]),
                },
            })
        return out

    def get_agent_patterns(self, agent_name: str, limit: int = 5) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT tool_sequence, confidence, ticker, fiscal_period, run_date
                       FROM investigation_patterns
                       WHERE agent_name = ?
                       ORDER BY confidence DESC, run_date DESC, id DESC
                       LIMIT ?""",
                    (agent_name, int(limit)),
                ).fetchall()
        except Exception as e:
            logger.warning(f"[MemoryLayer] get_agent_patterns failed: {e}")
            return []
        out = []
        for r in rows:
            try:
                tools = json.loads(r["tool_sequence"])
            except (json.JSONDecodeError, TypeError):
                tools = []
            out.append({
                "ticker": r["ticker"],
                "fiscal_period": r["fiscal_period"],
                "confidence": r["confidence"],
                "tool_sequence": tools,
                "run_date": r["run_date"],
            })
        return out

    def stats(self) -> dict:
        try:
            with self._connect() as conn:
                return {
                    "mistakes": conn.execute("SELECT COUNT(*) AS n FROM agent_mistakes").fetchone()["n"],
                    "data_gaps": conn.execute("SELECT COUNT(*) AS n FROM data_gaps").fetchone()["n"],
                    "patterns": conn.execute("SELECT COUNT(*) AS n FROM investigation_patterns").fetchone()["n"],
                    "inconsistencies": conn.execute("SELECT COUNT(*) AS n FROM narrative_inconsistencies").fetchone()["n"],
                }
        except Exception as e:
            logger.warning(f"[MemoryLayer] stats failed: {e}")
            return {"mistakes": 0, "data_gaps": 0, "patterns": 0, "inconsistencies": 0}

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _load_citations(citations_json: Optional[str]) -> list[dict]:
        if not citations_json:
            return []
        try:
            parsed = json.loads(citations_json)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    @staticmethod
    def _flatten_citations(citations: list) -> str:
        if not citations:
            return ""
        parts = []
        for c in citations:
            if isinstance(c, dict):
                doc = c.get("doc_id") or c.get("filename") or ""
                page = c.get("page")
                chunk = c.get("chunk_id")
                piece = doc
                if page is not None:
                    piece += f" p.{page}"
                if chunk:
                    piece += f" ({chunk})"
                if piece:
                    parts.append(piece.strip())
        return "; ".join(parts)[:500]


# ═══════════════════════════════════════════════════════════════════════════
# Module-level singleton
# ═══════════════════════════════════════════════════════════════════════════

_memory: Optional[MemoryLayer] = None


def get_memory() -> MemoryLayer:
    global _memory
    if _memory is None:
        _memory = MemoryLayer()
    return _memory
