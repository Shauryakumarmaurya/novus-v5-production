import os
os.environ['OBJC_DISABLE_INITIALIZE_FORK_SAFETY'] = 'YES'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
os.environ['no_proxy'] = '*'

# --- Fix WeasyPrint Library Loading on macOS ---
try:
    import cffi.api
    _orig_dlopen = cffi.api.FFI.dlopen

    def _patched_dlopen(self, name, flags=0):
        if isinstance(name, str) and not name.startswith('/'):
            paths_to_try = [
                f'/opt/homebrew/lib/{name}',
                f'/opt/homebrew/lib/{name}.dylib',
                f'/opt/homebrew/lib/{name}.0.dylib',
                f'/opt/homebrew/lib/{name.replace("-1.0-0", "-1.0.0.dylib")}',
                f'/opt/homebrew/lib/lib{name.replace("-1.0-0", "-1.0.0.dylib")}',
            ]
            for p in paths_to_try:
                if os.path.exists(p):
                    try:
                        return _orig_dlopen(self, p, flags)
                    except Exception:
                        pass
        return _orig_dlopen(self, name, flags)
    
    cffi.api.FFI.dlopen = _patched_dlopen
except ImportError:
    pass
# ----------------------------------------------

import functools
from utils.logger import get_logger
logger = get_logger(__name__)
from flask import Flask, Blueprint, request, jsonify, send_from_directory, send_file, stream_with_context
from dotenv import load_dotenv
import json
from redis_config import get_queue, get_redis  # updated import path (same directory)
from tasks import generate_financial_report, generate_financial_report_from_rag
from rq.job import Job
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import io
from pdf_export import generate_quant_pdf # updated import path (same directory)

# --- Lifted Lazy Imports ---
from flask import Response
from flask import send_file
from llm_clients import client, deepseek_model_name
from rag_engine import get_collection_stats
from rag_engine import ingest_documents
from rag_engine import query as rag_query, get_collection_stats
from screener_scraper import fetch_screener_tables
try:
    pass
    WEASYPRINT_AVAILABLE = True
except Exception as e:
    import logging
    logging.warning(f"WeasyPrint not available. PDF export disabled. Error: {e}")
    WEASYPRINT_AVAILABLE = False
import asyncio
import datetime
import glob
import io
import json as _json
import queue
import threading
import time as _time


# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__, static_folder='static')
app.json.sort_keys = False
# --- Blueprint Configuration ---
api_v1 = Blueprint('api_v1', __name__, url_prefix='/api/v1')


# --- Security: CORS restricted to allowed origins ---
ALLOWED_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5001,http://127.0.0.1:3000,http://127.0.0.1:5001"
).split(",")
CORS(app, origins=ALLOWED_ORIGINS)

# --- Security: Rate Limiting (Redis-backed so limits survive restarts/multi-worker) ---
def _limiter_storage_uri() -> str:
    explicit = os.getenv("RATELIMIT_STORAGE_URI")
    if explicit:
        return explicit
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis_url
    host = os.getenv("REDIS_HOST")
    if host:
        port = os.getenv("REDIS_PORT", "6379")
        db = os.getenv("REDIS_DB", "0")
        return f"redis://{host}:{port}/{db}"
    return "memory://"

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per hour"],
    storage_uri=_limiter_storage_uri(),
)

# --- Security: Upload limits ---
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024
MAX_UPLOAD_FILES = int(os.getenv("MAX_UPLOAD_FILES", "10"))

# --- Security: API Key Middleware (fails closed in production) ---
import hmac

API_KEY = os.getenv("NOVUS_API_KEY")
_ENV = (os.getenv("NOVUS_ENV") or os.getenv("FLASK_ENV") or "development").lower()
IS_PRODUCTION = _ENV == "production"

if IS_PRODUCTION and not API_KEY:
    raise RuntimeError(
        "NOVUS_API_KEY must be set when running in production "
        "(NOVUS_ENV/FLASK_ENV=production). Auth fails closed."
    )
if not API_KEY:
    logger.warning("NOVUS_API_KEY not set — API auth is DISABLED (dev mode only).")

def require_api_key(f):
    """Decorator to enforce X-API-Key header on protected endpoints."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if API_KEY:
            provided = request.headers.get("X-API-Key", "")
            if not hmac.compare_digest(provided, API_KEY):
                return jsonify({"error": "Unauthorized — invalid or missing X-API-Key header"}), 401
        elif IS_PRODUCTION:
            return jsonify({"error": "Server auth misconfigured — API key required"}), 503
        return f(*args, **kwargs)
    return decorated

# --- Security: Path Traversal Protection ---
ALLOWED_INGEST_PREFIXES = [
    p.strip() for p in
    os.getenv(
        "ALLOWED_INGEST_PATHS",
        os.path.expanduser("~/Desktop") + ",/data/uploads",
    ).split(",")
    if p.strip()
]

def resolve_allowed_folder(folder_path: str):
    """Resolve a user-supplied folder path against the ingest allowlist.

    Uses realpath() so `..` segments and symlinks cannot escape allowed roots.
    Returns the resolved absolute path, or None if not permitted.
    """
    try:
        resolved = os.path.realpath(os.path.expanduser(folder_path))
    except (TypeError, ValueError):
        return None
    for root in ALLOWED_INGEST_PREFIXES:
        root_resolved = os.path.realpath(os.path.expanduser(root))
        try:
            if os.path.commonpath([resolved, root_resolved]) == root_resolved:
                return resolved
        except ValueError:
            continue  # different drives / mixed abs-rel
    return None
@api_v1.route('/generate_report', methods=['POST'])
@require_api_key
@limiter.limit("10 per hour")
def generate_report():
    """Start background task for report generation"""
    if 'files' not in request.files or 'ticker' not in request.form:
        return jsonify({"error": "Missing files or ticker symbol"}), 400

    ticker = request.form['ticker']
    files = request.files.getlist('files')

    if not files or files[0].filename == '':
        return jsonify({"error": "No files selected"}), 400

    if len(files) > MAX_UPLOAD_FILES:
        return jsonify({"error": f"Too many files (max {MAX_UPLOAD_FILES} per request)"}), 413

    # Convert files to bytes for background processing
    files_data = []
    for file in files:
        files_data.append(file.read())

    # Queue the background task
    queue = get_queue()
    job = queue.enqueue(
        generate_financial_report,
        ticker,
        files_data,
        #retry=3,               # auto retries (requires rq.Retry)
        ttl=3600,               # job expires after 1h if not started
        result_ttl=3600,        # keep result for 1h
        failure_ttl=7200,       # keep failure info 2h
        job_timeout=600         # hard timeout 10 min
    )

    return jsonify({
        "job_id": job.id,
        "status": "queued",
        "message": "Report generation started. Use the job_id to check status."
    })


@api_v1.route('/analyze_rag', methods=['POST'])
@require_api_key
@limiter.limit("100 per hour")
def analyze_rag():
    """
    RAG-Only Analysis: Just provide a ticker.
    The system pulls all context from ChromaDB + Screener.in automatically.
    Body: { "ticker": "HUL" }
    """
    data = request.get_json()
    if not data or 'ticker' not in data:
        return jsonify({"error": "Missing 'ticker' in request body"}), 400

    ticker = data['ticker'].upper()

    # Queue the background task
    queue = get_queue()
    job = queue.enqueue(
        generate_financial_report_from_rag,
        ticker,
        ttl=3600,
        result_ttl=3600,
        failure_ttl=7200,
        job_timeout=900,          # 15 min timeout (RAG queries + LLM calls)
    )

    return jsonify({
        "job_id": job.id,
        "status": "queued",
        "ticker": ticker,
        "mode": "rag_only",
        "message": f"RAG-only analysis for {ticker} started. Use /job_status/{job.id} to check progress."
    })

@api_v1.route('/job_status/<job_id>')
@require_api_key
@limiter.exempt
def job_status(job_id):
    """Check the status of a background job"""
    try:
        job = Job.fetch(job_id, connection=get_redis())
    except Exception:
        return jsonify({"error": "job not found"}), 404

    # Reload from Redis so polling sees fresh meta (stage, agents) while the worker runs.
    if hasattr(job, "refresh"):
        job.refresh()

    status = job.get_status()
    # Map RQ statuses to frontend-friendly ones
    ui_status = (
        "completed" if status == "finished" else
        "processing" if status == "started" else
        status
    )
    payload = {
        "job_id": job.id,
        "status": ui_status,
        "enqueued_at": str(job.enqueued_at) if job.enqueued_at else None,
        "started_at": str(job.started_at) if job.started_at else None,
        "ended_at": str(job.ended_at) if job.ended_at else None,
        "progress": getattr(job, "meta", {}),
    }
    if status == "finished":
        payload["result"] = job.result
    if status == "failed":
        # Log the full traceback server-side; never leak stack traces to clients.
        logger.error(f"[JobStatus] job {job_id} failed:\n{job.exc_info}")
        payload["error"] = "Job failed. Check server logs for details."
    return jsonify(payload)

@app.route('/')
def serve_frontend():
    """Serve the main frontend"""
    response = send_from_directory(app.static_folder, 'index.html')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/health')
def health():
    try:
        get_redis().ping()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500

@api_v1.route('/screener_data', methods=['GET'])
@require_api_key
@limiter.limit("60 per hour")
def get_screener_data():
    """Fetch synchronous numerical table data from Screener.in"""
    ticker = request.args.get('ticker')
    if not ticker:
        return jsonify({"error": "Missing ticker parameter"}), 400
        
    try:
        data = fetch_screener_tables(ticker)
        if "error" in data:
            return jsonify(data), 500
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── RAG Chat Endpoint ────────────────────────────────────────────────────────

@api_v1.route('/chat', methods=['POST'])
@require_api_key
@limiter.limit("30 per minute")
def chat():
    """
    RAG-powered chat with Multi-Agent Validation Loop.
    """
    data = request.get_json()
    if not data or 'ticker' not in data or 'question' not in data:
        return jsonify({"error": "Missing 'ticker' or 'question'"}), 400

    ticker = data['ticker'].upper()
    question = data['question'].strip()
    history = data.get('history', [])

    if not question:
        return jsonify({"error": "Question cannot be empty"}), 400

    try:
        # Check if we have data for this ticker FIRST (fast fail)
        stats = get_collection_stats(ticker)
        if stats['total_chunks'] == 0:
            return jsonify({
                "answer": f"No documents found for **{ticker}** in the RAG store. Please ingest documents first using the /ingest_local endpoint.",
                "sources": [],
                "chunks_used": 0
            })

        # ── Lightweight conversational shortcut ──
        # Short chit-chat messages (greetings, thanks, "explain that again") don't
        # warrant spinning up the full ReAct agent. Kept cheap with a single V3 call.
        is_conversational = _is_conversational_shortcut(question)

        # --- SSE Generator ---
        def generate():
            try:
                if is_conversational:
                    yield from _stream_conversational(ticker, question, history)
                    return

                yield from _stream_agent_answer(ticker, question, history)

            except GeneratorExit:
                logger.info("[Chat] Client disconnected during SSE stream.")
            except Exception as e:
                logger.error(f"[Chat] Streaming Error: {e}")
                yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

        return Response(stream_with_context(generate()), mimetype="text/event-stream")

    except Exception as e:
        logger.error(f"Chat setup failed: {str(e)}")
        return jsonify({"error": f"Chat failed: {str(e)}"}), 500


# ═══════════════════════════════════════════════════════════════════════════
# Copilot helpers
# ═══════════════════════════════════════════════════════════════════════════

_CONVERSATIONAL_PATTERNS = (
    "hi", "hello", "hey", "yo", "thanks", "thank you", "thx", "ok", "okay",
    "cool", "got it", "nice", "great", "awesome", "clear", "understood",
    "explain that", "explain again", "clarify", "what do you mean",
)


def _is_conversational_shortcut(question: str) -> bool:
    """Cheap heuristic: short greeting/thanks messages bypass the agent."""
    q = (question or "").strip().lower().rstrip("!.?")
    if not q:
        return False
    if len(q.split()) > 6:
        return False
    return any(q == p or q.startswith(p + " ") for p in _CONVERSATIONAL_PATTERNS)


def _stream_conversational(ticker: str, question: str, history: list) -> "__import__('typing').Iterator[str]":
    """Send a natural conversational reply without invoking tools."""
    yield f"data: {json.dumps({'type': 'meta', 'sources': [], 'chunks_used': 0, 'ticker': ticker})}\n\n"
    yield f"data: {json.dumps({'type': 'clear'})}\n\n"

    system = (
        f"You are Novus Copilot, an institutional equity-research assistant "
        f"focused on {ticker}. The user sent a short conversational message. "
        "Reply politely and concisely (1-2 sentences). If appropriate, invite "
        "them to ask about red flags, narrative shifts, or specific financials."
    )
    messages = [{"role": "system", "content": system}]
    for m in history[-4:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})

    try:
        resp = client.chat.completions.create(
            model=deepseek_model_name,
            messages=messages,
            temperature=0.5,
            max_tokens=200,
        )
        answer = resp.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"[Chat] conversational LLM failed: {e}")
        answer = f"Hello. I'm Novus Copilot for {ticker}. Ask me about red flags, narrative shifts, or any metric you want to explore."

    for i in range(0, len(answer), 20):
        yield f"data: {json.dumps({'type': 'content', 'text': answer[i:i+20]})}\n\n"
        _time.sleep(0.02)
    yield "data: [DONE]\n\n"


def _stream_agent_answer(ticker: str, question: str, history: list) -> "__import__('typing').Iterator[str]":
    """Run CopilotAgentV3 in a background thread and stream ReAct progress +
    the final answer back over SSE. No retry loop, no entailment critic — the
    agent's tool calls ARE the grounding mechanism."""
    # Lazy imports so app boot doesn't require agents/memory at import time.
    from agents.copilot_agent import CopilotAgentV3
    from core.agent_base_v3 import AuditTrail
    from structured_data_fetcher import get_structured_data_fetcher
    from cio_orchestrator import _infer_fiscal_period

    # ── Gather financial tables + sector (cached by the fetcher) ──
    sector = "General"
    financial_tables: dict = {}
    fiscal_period = ""
    try:
        fetcher = get_structured_data_fetcher()
        sdata = fetcher.fetch(ticker)
        sector = sdata.get("sector") or "General"
        financial_tables = sdata.get("tables") or {}
        fiscal_period = _infer_fiscal_period(financial_tables)
    except Exception as e:
        logger.warning(f"[Chat] structured data fetch failed, proceeding without tables: {e}")

    # ── Progress plumbing: agent thread -> main thread via a Queue ──
    progress_q: queue.Queue = queue.Queue()
    SENTINEL = object()

    def on_step(step) -> None:
        # Fired by react_loop after every iteration. Keep the message short.
        if step.action:
            try:
                args = json.dumps(step.action_input or {}, default=str)
            except Exception:
                args = str(step.action_input)
            if len(args) > 100:
                args = args[:100] + "…"
            progress_q.put(f"> [Tool] {step.action}({args})\n\n")
        else:
            # Non-tool steps (e.g. thinking, final output) — keep silent or light
            thought = (step.thought or "").strip()
            if thought:
                progress_q.put(f"> [Thinking] {thought[:120]}\n\n")

    result_holder: dict = {}

    def run_agent() -> None:
        try:
            agent = CopilotAgentV3()
            trail: AuditTrail = agent.execute(
                ticker=ticker,
                document_text="",  # unused — search_document does RAG when ticker is set
                financial_tables=financial_tables,
                sector=sector,
                extraction_signals={
                    "_history": history,
                    "_question": question,
                    "_fiscal_period": fiscal_period,
                },
                llm=None,  # default V3 client
                dynamic_mandate="",
                fiscal_period=fiscal_period,
                on_step=on_step,
            )
            result_holder["trail"] = trail
        except Exception as e:
            logger.error(f"[Chat] CopilotAgentV3 crashed: {e}", exc_info=True)
            result_holder["error"] = str(e)
        finally:
            progress_q.put(SENTINEL)

    t = threading.Thread(target=run_agent, name="CopilotAgentV3", daemon=True)
    t.start()

    # Heartbeat / progress pump (hard wall-clock cap so a hung agent can't
    # hold the SSE connection / thread forever)
    CHAT_TIMEOUT_S = int(os.getenv("CHAT_TIMEOUT_S", "600"))
    deadline = _time.monotonic() + CHAT_TIMEOUT_S
    timed_out = False
    yield f"data: {json.dumps({'type': 'content', 'text': f'> [Copilot] Researching {ticker} with tools…\n\n'})}\n\n"
    while True:
        if _time.monotonic() > deadline:
            timed_out = True
            logger.error(f"[Chat] Copilot agent exceeded {CHAT_TIMEOUT_S}s for {ticker}; abandoning thread.")
            break
        try:
            item = progress_q.get(timeout=30)
        except queue.Empty:
            # Agent still working — emit a heartbeat to keep the UI alive
            yield f"data: {json.dumps({'type': 'content', 'text': '> [Copilot] Still working…\n\n'})}\n\n"
            continue
        if item is SENTINEL:
            break
        yield f"data: {json.dumps({'type': 'content', 'text': item})}\n\n"

    if timed_out:
        yield f"data: {json.dumps({'type': 'error', 'text': f'Copilot timed out after {CHAT_TIMEOUT_S}s. Please try a narrower question.'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    t.join(timeout=5)

    # ── Assemble final response ──
    if "error" in result_holder:
        yield f"data: {json.dumps({'type': 'error', 'text': result_holder['error']})}\n\n"
        yield "data: [DONE]\n\n"
        return

    trail: AuditTrail = result_holder.get("trail")
    findings = (trail.findings if trail else {}) or {}
    answer = (findings.get("answer") or "").strip()
    citations = findings.get("citations") or []
    tools_used = findings.get("tools_used") or []

    # Derive sources payload for the frontend from either citations[] or the
    # reasoning chain's tool observations (best-effort).
    sources: list = []
    seen_keys: set = set()
    for c in citations:
        if not isinstance(c, dict):
            continue
        doc_id = c.get("doc_id") or c.get("filename") or ""
        chunk_id = c.get("chunk_id") or ""
        key = f"{doc_id}|{chunk_id}"
        if not doc_id or key in seen_keys:
            continue
        seen_keys.add(key)
        sources.append({
            "filename": doc_id,
            "doc_type": c.get("doc_type", "unknown"),
            "section": c.get("section", ""),
            "chunk_id": chunk_id,
            "page": c.get("page"),
            "relevance": 1.0,
        })

    yield f"data: {json.dumps({'type': 'meta', 'sources': sources[:5], 'chunks_used': len(sources), 'ticker': ticker, 'tools_used': tools_used[:10]})}\n\n"
    yield f"data: {json.dumps({'type': 'clear'})}\n\n"

    # Fallback text if the agent failed to produce a valid answer JSON
    if not answer:
        answer = (
            "I wasn't able to produce a confident synthesis for that "
            "question. Try being more specific — e.g. 'What are the top 3 "
            "red flags in the FY24 numbers?' or 'Has management's demand "
            "commentary shifted across quarters?'"
        )

    for i in range(0, len(answer), 20):
        yield f"data: {json.dumps({'type': 'content', 'text': answer[i:i+20]})}\n\n"
        _time.sleep(0.02)
    yield "data: [DONE]\n\n"


# ── RAG Local Folder Ingestion ────────────────────────────────────────────────

@app.route('/ingest_local', methods=['POST'])
@require_api_key
@limiter.limit("10 per minute")
def ingest_local():
    """
    Ingest all PDFs from a local folder into the RAG vector store.
    Body: { "ticker": "RELIANCE", "folder_path": "/Users/.../Fin k10 copy" }
    """

    data = request.get_json()
    if not data or 'ticker' not in data:
        return jsonify({"error": "Missing 'ticker' in request body"}), 400

    ticker = data['ticker']
    folder_path = data.get('folder_path', os.path.expanduser('~/Desktop/Fin k10 copy'))

    # --- Security: Path traversal protection (realpath-based) ---
    folder_path = resolve_allowed_folder(folder_path)
    if folder_path is None:
        return jsonify({"error": "Folder path not permitted"}), 403

    if not os.path.isdir(folder_path):
        return jsonify({"error": f"Folder not found: {folder_path}"}), 404

    # Find all PDFs and CSVs in the folder (recursive)
    file_paths = glob.glob(os.path.join(folder_path, '**', '*.pdf'), recursive=True)
    file_paths += glob.glob(os.path.join(folder_path, '**', '*.PDF'), recursive=True)
    file_paths += glob.glob(os.path.join(folder_path, '**', '*.csv'), recursive=True)

    if not file_paths:
        return jsonify({"error": f"No PDF or CSV files found in {folder_path}"}), 404

    # Read files into (filename, bytes) pairs
    files_data = []
    for fpath in file_paths:
        try:
            with open(fpath, 'rb') as f:
                filename = os.path.basename(fpath)
                files_data.append((filename, f.read()))
        except Exception as e:
            logger.info(f"[Ingest] Skipped {fpath}: {e}")

    if not files_data:
        return jsonify({"error": "Could not read any files"}), 500

    # Ingest into RAG
    try:
        result = ingest_documents(ticker, files_data)
        return jsonify({
            "status": "success",
            "ticker": ticker,
            "folder": folder_path,
            "files_processed": len(files_data),
            "filenames": [f[0] for f in files_data],
            **result,
        })
    except Exception as e:
        return jsonify({"error": f"Ingestion failed: {str(e)}"}), 500


@api_v1.route('/tickers')
@require_api_key
def list_tickers():
    """List tickers available in the RAG store (drives the UI ticker dropdown)."""
    try:
        from rag_engine import list_ingested_tickers
        return jsonify({"tickers": list_ingested_tickers()})
    except Exception as e:
        logger.error(f"[Tickers] listing failed: {e}")
        return jsonify({"error": "Could not list tickers"}), 500


@app.route('/rag_stats/<ticker>')
@require_api_key
def rag_stats(ticker):
    """Get RAG store stats for a ticker."""
    try:
        stats = get_collection_stats(ticker.upper())
        return jsonify({"ticker": ticker.upper(), **stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/list_local_pdfs', methods=['POST'])
@require_api_key
@limiter.limit("30 per minute")
def list_local_pdfs():
    """List PDFs in a local folder (preview before ingesting)."""

    data = request.get_json() or {}
    folder_path = data.get('folder_path', os.path.expanduser('~/Desktop/Fin k10 copy'))

    # Same allowlist policy as /ingest_local
    folder_path = resolve_allowed_folder(folder_path)
    if folder_path is None:
        return jsonify({"error": "Folder path not permitted"}), 403

    if not os.path.isdir(folder_path):
        return jsonify({"error": f"Folder not found: {folder_path}"}), 404

    pdf_paths = glob.glob(os.path.join(folder_path, '**', '*.pdf'), recursive=True)
    pdf_paths += glob.glob(os.path.join(folder_path, '**', '*.PDF'), recursive=True)

    files_info = []
    for p in sorted(set(pdf_paths)):
        stat = os.stat(p)
        files_info.append({
            "filename": os.path.basename(p),
            "path": p,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
        })

    return jsonify({
        "folder": folder_path,
        "total_pdfs": len(files_info),
        "files": files_info,
    })

@app.route('/export_pdf', methods=['POST'])
@require_api_key
@limiter.limit("20 per hour")
def export_pdf():
    """Generate a professional, print-ready PDF using WeasyPrint."""
    data = request.get_json()
    if not data or 'content_html' not in data:
        return jsonify({"error": "Missing 'content_html' in request"}), 400
        
    ticker = data.get('ticker', 'REPORT').upper()
    content_html = data['content_html']
    
    # Build the "as of" date for the report header
    from datetime import date
    report_date = date.today().strftime("%B %d, %Y")
    
    # Wrap in minimal HTML with professional research-report typography
    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <style>
            @page {
                margin: 2cm 2cm 2.5cm 2cm;
                @top-left {
                    content: "{ticker} — Novus Research Report";
                    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                    font-size: 7.5pt;
                    color: #888;
                    letter-spacing: 0.5pt;
                }
                @top-right {
                    content: "As of {report_date}";
                    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                    font-size: 7.5pt;
                    color: #888;
                }
                @bottom-center {
                    content: "{ticker} — p." counter(page) " of " counter(pages);
                    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                    font-size: 7.5pt;
                    color: #888;
                    letter-spacing: 0.5pt;
                }
            }
            @page :first {
                @top-left { content: none; }
                @top-right { content: none; }
            }
            body {
                font-family: "Georgia", "Times New Roman", serif;
                font-size: 10.5pt;
                line-height: 1.55;
                color: #1a1a1a;
                background: #fff;
            }
            /* ── Report Title Block ── */
            .report-header {
                text-align: center;
                border-bottom: 2.5px solid #111;
                padding-bottom: 14px;
                margin-bottom: 24px;
            }
            .report-header h1 {
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 20pt;
                font-weight: 700;
                letter-spacing: 1.5pt;
                margin: 0 0 4px 0;
                border: none;
                padding: 0;
                color: #000;
            }
            .report-header .subtitle {
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 9pt;
                color: #666;
                letter-spacing: 1pt;
                text-transform: uppercase;
            }
            .report-header .date {
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 8pt;
                color: #999;
                margin-top: 2px;
            }
            /* ── Typography Hierarchy ── */
            h1 {
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 16pt;
                font-weight: 700;
                color: #000;
                border-bottom: 1.5px solid #333;
                padding-bottom: 4px;
                margin-top: 28px;
                margin-bottom: 10px;
                page-break-after: avoid;
            }
            h2 {
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 13pt;
                font-weight: 600;
                color: #111;
                border-bottom: 1px solid #ccc;
                padding-bottom: 3px;
                margin-top: 22px;
                margin-bottom: 8px;
                page-break-after: avoid;
            }
            h3 {
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 11pt;
                font-weight: 600;
                color: #222;
                margin-top: 16px;
                margin-bottom: 6px;
                border: none;
                padding: 0;
                page-break-after: avoid;
            }
            h4 {
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 10pt;
                font-weight: 600;
                color: #333;
                margin-top: 12px;
                margin-bottom: 4px;
                border: none;
                padding: 0;
                page-break-after: avoid;
            }
            p {
                margin-bottom: 8px;
                orphans: 3;
                widows: 3;
            }
            ul, ol {
                margin-left: 1.2em;
                margin-bottom: 10px;
            }
            li {
                margin-bottom: 4px;
            }
            strong {
                font-weight: 700;
            }
            /* ── Code / Metric badges ── */
            pre, code {
                font-family: "Courier New", Courier, monospace;
                font-size: 8.5pt;
                background: #f5f5f5;
                padding: 2px 4px;
                border-radius: 2px;
            }
            pre {
                padding: 10px;
                border: 1px solid #e0e0e0;
                overflow-x: auto;
                page-break-inside: avoid;
            }
            .calc-badge {
                font-family: monospace;
                font-weight: bold;
                background: #eee;
                padding: 1px 3px;
                border: 1px solid #ccc;
            }
            /* ── Tables ── */
            table {
                width: 100%;
                border-collapse: collapse;
                margin: 14px 0;
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 8.5pt;
                page-break-inside: avoid;
            }
            th, td {
                border: 1px solid #d0d0d0;
                padding: 6px 8px;
                text-align: left;
            }
            th {
                background-color: #f0f0f0;
                font-weight: 600;
            }
            tr:nth-child(even) {
                background-color: #fafafa;
            }
            /* ── Disclaimer ── */
            .disclaimer {
                margin-top: 30px;
                padding-top: 12px;
                border-top: 1px solid #ccc;
                font-size: 7.5pt;
                color: #999;
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                line-height: 1.4;
            }
            /* ── Tearsheet CSS ── */
            .tearsheet {
                margin-bottom: 24px;
                padding-bottom: 16px;
                border-bottom: 2px solid #111;
                page-break-after: avoid;
            }
            .thesis-statement {
                font-size: 13pt;
                font-weight: 700;
                line-height: 1.4;
                margin-bottom: 16px;
                color: #000;
                border-left: 4px solid #111;
                padding-left: 12px;
            }
            .rating-strip {
                display: flex;
                justify-content: space-between;
                align-items: center;
                background: #f8f9fa;
                border: 1px solid #e5e7eb;
                padding: 10px 14px;
                border-radius: 4px;
                margin-bottom: 20px;
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
            }
            .rating-chip {
                font-size: 11pt;
                font-weight: 700;
                padding: 4px 10px;
                border-radius: 4px;
                color: #fff;
                text-transform: uppercase;
            }
            .rating-chip.buy { background-color: #059669; }
            .rating-chip.hold { background-color: #d97706; }
            .rating-chip.sell { background-color: #dc2626; }
            .rating-chip.pass { background-color: #4b5563; }
            
            .kpi-grid {
                display: table;
                width: 100%;
                table-layout: fixed;
                border-collapse: separate;
                border-spacing: 8px 0;
                margin-bottom: 20px;
            }
            .kpi-cell {
                display: table-cell;
                background: #fff;
                border: 1px solid #e5e7eb;
                border-radius: 4px;
                padding: 10px;
                text-align: center;
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
            }
            .kpi-label {
                font-size: 7.5pt;
                color: #6b7280;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-bottom: 4px;
            }
            .kpi-value {
                font-size: 14pt;
                font-weight: 700;
                color: #111;
            }
            .kpi-sub {
                font-size: 7pt;
                color: #9ca3af;
                margin-top: 2px;
            }
            .scoreboard-grid {
                display: table;
                width: 100%;
                margin-bottom: 20px;
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 9pt;
            }
            .scoreboard-row {
                display: table-row;
            }
            .scoreboard-label {
                display: table-cell;
                padding: 6px 0;
                border-bottom: 1px solid #f3f4f6;
                color: #4b5563;
                width: 70%;
            }
            .scoreboard-val {
                display: table-cell;
                padding: 6px 0;
                border-bottom: 1px solid #f3f4f6;
                text-align: right;
                font-weight: 700;
            }
            .color-green { color: #059669; }
            .color-amber { color: #d97706; }
            .color-red { color: #dc2626; }
            
            .charts-grid {
                margin-top: 20px;
                text-align: center;
            }
            .chart-img {
                max-width: 48%;
                display: inline-block;
                margin: 1%;
                border: 1px solid #eee;
                border-radius: 4px;
            }
            .chart-img-full {
                max-width: 98%;
                display: block;
                margin: 0 auto 12px auto;
                border: 1px solid #eee;
                border-radius: 4px;
            }
            
            /* ── Suppress dark-mode UI artifacts ── */
            .bg-semantic-amber\\/20, .bg-semantic-green\\/20 { background: transparent !important; }
            span[class*="semantic"] { color: inherit !important; border: none !important; }
        </style>
    </head>
    <body>
        <div class="report-header">
            <h1>{ticker}</h1>
            <div class="subtitle">Novus Institutional Equity Research</div>
            <div class="date">{report_date}</div>
        </div>
        {content_html}
        <div class="disclaimer">
            <strong>Disclaimer:</strong> This report was generated by the Novus Multi-Agent System (MAS). 
            It is for informational purposes only and does not constitute investment advice, a recommendation, 
            or a solicitation to buy or sell any security. All data is derived from publicly available filings 
            and earnings transcripts. Novus makes no representation regarding the accuracy or completeness of 
            the information herein. Past performance is not indicative of future results.
        </div>
    </body>
    </html>
    """
    
    # Build the Tearsheet HTML if raw_data is provided
    tearsheet_html = ""
    signal_html = ""
    raw_data = data.get('raw_data')
    charts = data.get('charts', {})
    
    if raw_data:
        # Extract PM findings
        agent_trails = raw_data.get('agent_trails', {})
        pm_trail = agent_trails.get('pm_synthesis', {})
        findings = pm_trail.get('findings', {})
        
        thesis = findings.get('executive_summary', 'No executive summary provided.')
        recommendation = findings.get('recommendation', 'N/A').upper()
        
        # Color code rating chip
        chip_class = "pass"
        if "ADD" in recommendation or "BUY" in recommendation or "CONSTRUCTIVE" in recommendation: chip_class = "buy"
        elif "SELL" in recommendation or "SHORT" in recommendation: chip_class = "sell"
        elif "HOLD" in recommendation or "NEUTRAL" in recommendation: chip_class = "hold"
        
        # KPIs from forensic scorecard
        fs = raw_data.get('forensic_scorecard', {})
        kpi_html = f"""
        <div class="kpi-grid">
            <div class="kpi-cell">
                <div class="kpi-label">ROIC vs WACC</div>
                <div class="kpi-value">{fs.get('roic_latest', 'N/A')}</div>
            </div>
            <div class="kpi-cell">
                <div class="kpi-label">Net Debt / EBITDA</div>
                <div class="kpi-value">{fs.get('net_debt_ebitda', 'N/A')}</div>
            </div>
            <div class="kpi-cell">
                <div class="kpi-label">OCF / EBITDA</div>
                <div class="kpi-value">{fs.get('ocf_ebitda_ratio', 'N/A')}</div>
            </div>
            <div class="kpi-cell">
                <div class="kpi-label">Revenue CAGR</div>
                <div class="kpi-value">{fs.get('revenue_cagr_3y', 'N/A')}</div>
            </div>
            <div class="kpi-cell">
                <div class="kpi-label">Net Profit CAGR</div>
                <div class="kpi-value">{fs.get('net_profit_cagr_3y', 'N/A')}</div>
            </div>
        </div>
        """
        
        # Scoreboard
        scoreboard = findings.get('scoreboard', {})
        scoreboard_rows = ""
        for key, val in scoreboard.items():
            if val is None or str(val).strip() == "": continue
            label = key.replace("_", " ").title()
            val_str = str(val).upper()
            color_class = ""
            if val_str in ["A", "A+", "B+", "STRONG", "FAIR"]: color_class = "color-green"
            elif val_str in ["C", "C+", "B-", "WEAKENING", "CAUTION"]: color_class = "color-amber"
            elif val_str in ["D", "F", "WEAK", "POOR", "AVOID", "HIGH RISK"]: color_class = "color-red"
            
            scoreboard_rows += f"""
            <div class="scoreboard-row">
                <div class="scoreboard-label">{label}</div>
                <div class="scoreboard-val {color_class}">{val_str}</div>
            </div>
            """
            
        scoreboard_html = f'<div class="scoreboard-grid">{scoreboard_rows}</div>'
        
        # Charts HTML
        charts_html = '<div class="charts-grid">'
        for chart_id, b64 in charts.items():
            # If it's the timeline or a large chart, make it full width
            img_class = 'chart-img-full' if 'timeline' in chart_id.lower() or 'narrative' in chart_id.lower() else 'chart-img'
            charts_html += f'<img src="{b64}" class="{img_class}" />'
        charts_html += '</div>'
        
        # Signal Intelligence Panel
        signal_payload = raw_data.get('signal_payload', {})
        if signal_payload and isinstance(signal_payload, dict):
            signals = signal_payload.get("signals", [])
            impacts = signal_payload.get("impacts", [])
            unavailable = signal_payload.get("unavailable_sources", [])
            
            if signals or impacts:
                from datetime import datetime
                now_ist = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                signal_html += f"<h2>On-Demand Signal Intelligence</h2>"
                signal_html += f"<p><em>Signals as of {now_ist}.</em></p>"
                if unavailable:
                    signal_html += f"<p><strong>Note:</strong> Unavailable sources at fetch time: {', '.join(unavailable)}</p>"
                    
                signal_html += "<h3>Live Signals</h3><ul>"
                for sig in signals:
                    dir_class = "color-green" if sig.get("direction") == "positive" else "color-red" if sig.get("direction") == "negative" else "color-amber"
                    signal_html += f"<li><strong class='{dir_class}'>[{sig.get('category', '').upper()}]</strong> {sig.get('summary', '')} <em>(Score: {sig.get('materiality_score', '')})</em></li>"
                signal_html += "</ul>"
                
                signal_html += "<h3>Thesis Impact</h3><ul>"
                for imp in impacts:
                    dir_class = "color-green" if imp.get("direction") == "positive" else "color-red" if imp.get("direction") == "negative" else "color-amber"
                    kill_flag = f" <strong>[KILL CRITERION TRIGGERED: {imp.get('triggers_kill_criterion_id')}]</strong>" if imp.get("triggers_kill_criterion_id") else ""
                    signal_html += f"<li><strong class='{dir_class}'>[{imp.get('qualitative_magnitude', '').upper()} IMPACT]</strong> Affects: {', '.join(imp.get('affected_thesis_drivers', []))}. Horizon: {imp.get('horizon', '')}. Watch: {imp.get('what_to_watch', '')}.{kill_flag}</li>"
                signal_html += "</ul>"
        
        tearsheet_html = f"""
        <div class="tearsheet">
            <div class="thesis-statement">{thesis}</div>
            <div class="rating-strip">
                <div><strong>Recommendation:</strong> <span class="rating-chip {chip_class}">{recommendation}</span></div>
            </div>
            {kpi_html}
            {scoreboard_html}
            {charts_html}
        </div>
        """
        
    try:
        # Prepend tearsheet and append signals to content_html
        full_html = tearsheet_html + content_html + signal_html
        final_html = html_template.replace("{ticker}", ticker).replace("{report_date}", report_date).replace("{content_html}", full_html)
        
        # Send to Gotenberg container (running locally on port 3000 via docker-compose)
        files = {
            'files': ('index.html', final_html)
        }
        data = {
            'paperWidth': 8.27,
            'paperHeight': 11.69,
            'marginTop': 0.5,
            'marginBottom': 0.5,
            'marginLeft': 0.5,
            'marginRight': 0.5,
            'printBackground': True
        }
        import requests
        gotenberg_url = os.getenv("GOTENBERG_URL", "http://gotenberg:3000")
        response = requests.post(f"{gotenberg_url}/forms/chromium/convert/html", files=files, data=data, timeout=30)
        response.raise_for_status()
        pdf_bytes = response.content
        
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'{ticker}_Novus_Analysis.pdf'
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500




app.register_blueprint(api_v1)

if __name__ == '__main__':
    app.run(port=5001, debug=False, host='0.0.0.0')
