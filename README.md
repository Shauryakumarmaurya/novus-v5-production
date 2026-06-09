# Novus — Institutional Equity Research Engine

Financial LLM stack for Indian equity research (mutual funds, AMCs, PMs).
Deterministic, audit-first extraction: numbers and causal claims must trace to
tools or document chunks — see [architecture.md](architecture.md) for the
engineering constitution.

## Stack

| Concern | Implementation |
|---------|----------------|
| API + UI | Flask (`app.py`) serving the static UI at `/` (`static/index.html` + `static/js/novus-*.js`) |
| Jobs | Redis + RQ (`tasks.py`, `worker.py`) |
| Orchestration | `cio_orchestrator.py` — parallel specialists → Auditor (critic) → PM synthesis |
| Copilot chat | `agents/copilot_agent.py` ReAct loop, SSE via `POST /api/v1/chat` |
| RAG | ChromaDB + voyage-finance-2 (`rag_engine.py`), time-aware fiscal filters |
| Memory | SQLite WAL (`data/novus_master.db`, `core/memory.py`) |
| Structured data | Screener.in scrape (`structured_data_fetcher.py`, `screener_scraper.py`) |
| PDF export | Gotenberg (`/export_pdf`) |

The Next.js app previously in `frontend/` is archived at
`_archive/frontend-next/` — it targeted a `/api/v1/research/*` API that was
never built. The static UI is canonical.

## Quick start (local)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in keys (DeepSeek, Voyage, NOVUS_API_KEY, ...)

docker run -p 6379:6379 redis:7-alpine   # Redis

./run_dev.sh           # starts worker + Flask on http://localhost:5001
```

## Auth

All data-bearing endpoints require the `X-API-Key` header matching
`NOVUS_API_KEY`. In production (`NOVUS_ENV=production` or
`FLASK_ENV=production`) the app refuses to boot without a key (fails closed).
In the browser UI, open `/?api_key=<key>` once — it's stored in localStorage.

## Onboarding a company

```bash
# 1. Drop PDFs (annual reports, transcripts, quarterly results) into:
#    data/raw/<TICKER>/
python onboard_tenant.py SUNPHARMA
# or point at any folder:
python onboard_tenant.py SUNPHARMA --folder /path/to/pdfs
```

Everything ingests into ChromaDB (`chroma_db/`) — the same store the copilot
and report pipeline query. The UI ticker dropdown is driven by
`GET /api/v1/tickers` (whatever is actually ingested).

## Keeping data fresh

```bash
# cron-able: re-scrapes financials + ingests new files in data/raw/<TICKER>/
python scripts/scheduled_refresh.py
# crontab example (02:30 daily):
# 30 2 * * * cd /path/to/repo && venv/bin/python scripts/scheduled_refresh.py >> data/refresh.log 2>&1
```

Bulk Screener document downloads (`scrapper/scrape_screener_docs.py`) need
`SCREENER_SESSIONID` / `SCREENER_CSRFTOKEN` in `.env`.

## API summary

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/generate_report` | Upload PDFs + ticker → queued deep-dive job |
| `POST /api/v1/analyze_rag` | RAG-only deep dive (no upload) |
| `GET /api/v1/job_status/<id>` | Poll job progress / result |
| `POST /api/v1/chat` | Copilot SSE chat |
| `GET /api/v1/tickers` | Ingested ticker universe |
| `GET /api/v1/screener_data?ticker=` | Structured financial tables |
| `POST /ingest_local` | Ingest a local folder (allowlisted paths only) |
| `GET /rag_stats/<ticker>` | RAG store stats |
| `POST /export_pdf` | Render report PDF via Gotenberg |
| `GET /health` | Liveness (no auth) |

## Tests

```bash
python -m pytest tests/ -q
```

CI (`.github/workflows/deploy.yml`) runs the suite on every push to `main`
and only deploys to the Azure VM (Docker Compose + Caddy) if it passes.

## Deployment

Production is Docker Compose on an Azure VM behind Caddy
(`docker-compose.yml`, `Caddyfile`, `deploy_azure.sh`). Set `NOVUS_API_KEY`
and `CORS_ORIGINS` in the VM environment — compose fails fast without the key.
