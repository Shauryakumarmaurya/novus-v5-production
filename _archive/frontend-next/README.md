# ARCHIVED — Next.js frontend (not the production UI)

**Status: archived.** The canonical, production UI for Novus is the static app
served by Flask at `/` (`static/index.html` + `static/js/novus-*.js`).

## Why this is archived

This Next.js app was built against a human-in-the-loop research API
(`POST /api/v1/research/initiate`, `/research/execute` (SSE),
`/research/challenge`) that **was never implemented in `app.py`**. Every
network call it makes 404s against the current backend, which uses a
job-queue model instead (`/api/v1/generate_report` → `/api/v1/job_status/<id>`).

It also lacks most of the shipped product surface: PDF upload, RAG-only
analysis, screener tables, charts, live signals, and PDF export.

## If you want to revive it

1. Implement the `/api/v1/research/*` endpoints in `app.py` (initiate returns
   an audit plan + assumptions; execute streams agent progress over SSE;
   challenge re-runs a single agent with a user objection).
2. Add `X-API-Key` headers to all fetches in `src/store/useReportStore.ts`
   (the backend now fails closed in production).
3. Reach feature parity with the static UI before switching `/` over.
4. Add it to `docker-compose.yml` / Caddy, and remove `frontend/` from
   `.dockerignore`.
