# Trench — Phase 1: Project Setup — Design Spec

Date: 2026-09-09
Status: Approved

## Purpose

Stand up the skeleton of Trench (personal knowledge base / Agentic RAG project)
so that backend, database, and frontend are wired together and provably
working, before any RAG-specific design begins. This phase produces no RAG
features — it proves the plumbing.

## Scope

In scope:
- `trench_backend/`: FastAPI app, async SQLAlchemy + PostgreSQL, Alembic,
  config/logging, one real endpoint (`GET /health`) that checks DB
  connectivity.
- `trench_frontend/`: Next.js 16 (App Router) + React 19, Tailwind v4,
  shadcn/radix-ui, next-themes, zustand, react-query, axios,
  react-hook-form + zod. Light/dark theme derived from the reference
  palette. A `/settings` page with a working theme toggle. A landing page
  that calls the backend `/health` endpoint.
- Git repo init at `/home/ib-80/Desktop/Trench` with `.gitignore` excluding
  secrets, build artifacts, and the two reference-only files
  (`frontend.html`, the Firebase admin SDK JSON).
- uv-managed Python project (pyproject.toml, lockfile).

Out of scope (future phases):
- Firebase Authentication (Phase 2).
- Any Agentic RAG component (ingestion, embeddings, vector DB, LangGraph,
  MCP, etc.) — separate spec after Phase 1 is verified.

## Architecture

```
Trench/
  trench_backend/
    app/
      api/          # routers (health.py for now)
      core/          # settings (pydantic-settings), logging
      db/            # async engine, session factory, Base
      models/        # SQLAlchemy ORM models
      schemas/       # Pydantic schemas
      main.py        # FastAPI app factory
    alembic/
    pyproject.toml
    .env.example
  trench_frontend/
    src/
      app/           # Next.js App Router routes (/, /settings)
      components/    # shared UI components (shadcn-based)
      lib/           # api client (axios), query client
      styles/        # theme tokens (light/dark CSS vars)
    package.json
  docs/superpowers/specs/
  .gitignore
```

## Data flow (Phase 1)

1. Frontend landing page mounts → react-query calls `GET /health` via axios.
2. FastAPI `health` router opens an async DB session → runs `SELECT 1`.
3. Response `{status: "ok", db: "connected"}` returned → rendered on page.
4. Theme toggle on `/settings` updates next-themes context → persisted
   (localStorage) → CSS vars swap between light/dark token sets derived
   from the reference palette (`#D3FB52`, `#273F43`, `#052326`, `#C7FBEA`,
   `#D3FBF6`, `#EDFCD6`).

## Configuration & secrets

- Backend reads `DATABASE_URL` and app settings from `.env` via
  pydantic-settings. `.env` is gitignored; `.env.example` is committed with
  placeholder values.
- `trench-e4b2f-firebase-adminsdk-*.json` and `frontend.html` stay in the
  repo root but are explicitly gitignored by filename — reference-only,
  never committed. Firebase Admin key rotation happens in Phase 2, before
  it is wired into actual auth code.

## Testing / verification

- Backend: `uv run uvicorn app.main:app --reload`, curl `/health`, confirm
  DB-connected status.
- Frontend: `npm run dev`, confirm landing page shows backend health
  status, confirm `/settings` theme toggle switches and persists across
  reload.
- No automated test suite yet (nothing with real logic to test at this
  phase) — verification is manual smoke-testing of the wiring.

## Alternatives considered

- **Feature-based backend layout** instead of layered-by-concern: rejected
  for Phase 1 — no features exist yet besides health, so feature folders
  would be empty scaffolding with no payoff until the RAG phase.
- **Minimal frontend toolkit** (Tailwind + next-themes only, add shadcn/
  zustand/react-query later): rejected — user preferred adopting the full
  Abyss toolkit now to avoid retrofitting state/data-fetching libraries
  mid-feature later.

## Documentation deliverable

After Phase 1 lands, a companion doc will cover: async SQLAlchemy
internals (pooling, session lifecycle), why uv, why this backend layout,
why Next.js App Router + next-themes, and likely interview questions —
per the project's stated interview-prep goal.
