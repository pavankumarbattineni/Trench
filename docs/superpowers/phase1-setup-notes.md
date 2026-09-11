# Trench Phase 1 — Setup: Implementation Notes & Interview Prep

> **Note on staleness:** this doc captures the *reasoning* behind Phase 1's
> setup choices, which mostly still holds. Two concrete things it originally
> described have since changed and are corrected inline below: the backend's
> folder layout (restructured in Phase 2 from `api/core/db` to
> `router/service/database/utils/schemas/middleware`) and the color palette
> (moved from a pale-green/mint base to a white/black base with a single
> lime accent, during a later design pass). See
> `docs/superpowers/phase2-auth-notes.md` for the restructure's own
> reasoning.

## What was built

A FastAPI backend (async SQLAlchemy + PostgreSQL, Alembic-managed) exposing
one real endpoint, `GET /health`, which proves DB connectivity by running
`SELECT 1` through the async session. A Next.js 16 frontend (App Router,
React 19) renders that status live on its landing page and provides a
`/settings` page with a persisted light/dark theme toggle built on
next-themes and shadcn/base-ui primitives. Verified end-to-end in a real
headless-Chromium session: landing page renders
`Backend status: ok · DB: connected`, the theme toggle switches the whole
page between light and dark token sets, and the choice survives a full
page reload via `localStorage`.

## Request flow

1. Browser loads `/` → React Query's `useQuery` calls `getHealth()`
   (`src/lib/api.ts`), which issues `axios.get("/health")` against the
   FastAPI base URL.
2. FastAPI's `health_check` handler receives the request, and its
   `db: AsyncSession = Depends(get_db)` dependency triggers `get_db()`
   (`app/database/session.py` — moved here from `app/db/session.py` in the
   Phase 2 restructure), which opens a session from
   `async_session_factory` — itself backed by `create_async_engine`, which
   owns a connection pool to PostgreSQL (via `asyncpg`).
3. `SELECT 1` executes over that pooled connection; on success, the
   handler returns `HealthResponse(status="ok", db="connected")`, a
   Pydantic model FastAPI serializes to JSON.
4. React Query caches the response and the component renders it. No
   loading spinner libraries needed — `isLoading`/`isError`/`data` come
   straight from `useQuery`'s return.

## Why async SQLAlchemy

FastAPI is an ASGI framework built on an event loop; a synchronous DB
driver would block that loop on every query, serializing requests that
should run concurrently. `asyncpg` + SQLAlchemy's async engine let the
event loop hand off to other coroutines while waiting on I/O — the same
reason ThinkLoop (per your resume) is built async end-to-end. The
tradeoff: async code can't share a session across concurrent tasks safely
(sessions aren't thread/task-safe), which is why `get_db()` opens a fresh
session per request via FastAPI's dependency system rather than a
module-level shared session.

## Why uv

`uv` resolves and installs faster than pip/poetry (written in Rust, uses a
global cache), manages the Python interpreter version itself
(`--python 3.13`), and produces a lockfile (`uv.lock`) for reproducible
installs — comparable to what `package-lock.json` does for npm.
One gotcha we hit: `uv init --app` still defaults to a *packaged* project
(a `src/<name>/` layout plus a `[build-system]` block and its own nested
git repo) unless you also pass `--no-package --vcs none`. Packaging makes
sense for a library you'll publish to PyPI; for an application you run
in place, it's pure overhead — the flat `app/` layout is simpler to
navigate and there's nothing to build/publish.

## Why this backend layout (layered-by-concern)

**Superseded in Phase 2:** Phase 1 shipped with `api/`, `core/`, `db/`,
`models/`, `schemas/`. When Phase 2 added authentication, the layout was
restructured to `router/`, `service/`, `database/`, `utils/`, `schemas/`,
plus a new `middleware/` — matching a reference codebase's conventions
(routers only orchestrate request/response, all business logic lives in
`service/` classes, `database/` owns models and session/engine setup,
`utils/` holds reusable framework-agnostic helpers). The original
Phase 1 reasoning still applies to *why layer at all* at this size: it
keeps each file's responsibility obvious, and a feature-based layout
(each domain owns its own router+model+schema) only starts winning once
there are multiple real domains to separate — still deferred, since
Trench has one domain (auth) so far.

## Why Next.js App Router + next-themes

The App Router is Next's current model (layouts, server components by
default, colocated route files) and matches the Abyss reference project,
so patterns/conventions transfer directly. `next-themes` handles the
classic SSR theming problem: the server doesn't know the client's stored
theme preference, so a naive implementation flashes the wrong theme on
load ("FOUC" for theme). `next-themes` solves this with a small inline
script injected before hydration that sets the `class` attribute on
`<html>` before paint, and `suppressHydrationWarning` on `<html>` silences
the one hydration warning React would otherwise raise for that
intentional mismatch. We confirmed this in practice: after toggling to
dark and reloading, `document.documentElement.className` already
contained `dark` on the very first render we inspected.

Note: the shadcn CLI used here scaffolds components on top of `@base-ui/
react` rather than Radix in this version — functionally equivalent for
our purposes (both are unstyled, accessible primitive libraries), but if
you see `@base-ui/react` imports in `src/components/ui/*`, that's why —
not a mistake, just a newer default in the shadcn generator.

**Palette, superseded:** Phase 1's tokens used a pale-green/mint page
background (`#EDFCD6`) sampled from a reference image, with the lime
accent (`#D3FB52`) reserved for buttons and highlights. A later design
pass moved the *background* to plain white (light) / black (dark), and
promoted that same lime to the app's one consistent accent color used
everywhere (buttons, focus rings, status indicators) — a more
conventional light/dark base with the brand color doing all the work as
a deliberate accent, rather than tinting the whole page. All of this
still flows through the same CSS custom properties in `globals.css`
(`--background`, `--primary`, etc.), so no component code needed to
change — only the token *values* did.

## Alembic — why wire it up now, with zero models

Migrations are a habit, not a checkpoint you add later. Wiring Alembic
against an empty `Base.metadata` now means the *first* migration for
Phase 2 (a `users` or `sessions` table for Firebase-linked profiles, most
likely) is a normal autogenerate diff against a known-working pipeline,
not a from-scratch setup done under feature pressure.

## A concrete async gotcha we hit and fixed

Running the three backend tests together initially failed with
`RuntimeError: Event loop is closed`, even though each test passed in
isolation. Cause: `pytest-asyncio`'s default is a **fresh event loop per
test function**, but `app/database/session.py` (`app/db/session.py` at
the time) creates its `engine` (and the pool of connections it holds)
once, at import time, bound to whichever
loop existed first. When the next test spun up a new loop, the pool's
already-open connections belonged to a now-closed loop.

Fix: set `asyncio_default_fixture_loop_scope = "session"` and
`asyncio_default_test_loop_scope = "session"` in `pyproject.toml`, so all
tests in a run share one event loop — which also matches production
reality (a FastAPI app has exactly one event loop for its entire
lifetime; the engine is never expected to survive across loops). This is
a genuinely common async SQLAlchemy pitfall worth being able to explain,
not just fix.

## Interview questions this phase prepares you for

- "Walk me through what happens between a browser request and a DB query
  in your FastAPI app." → the request flow above.
- "Why async over sync SQLAlchemy? What are the gotchas?" → event loop
  blocking; sessions aren't safely shared across concurrent tasks; the
  `pool_pre_ping=True` detail (why: recycles dead connections after DB
  restarts/idle timeouts, at the cost of one extra round-trip per
  checkout); and the event-loop-per-test pitfall above, which is really
  the same "sessions/engines are loop-scoped" rule showing up in tests.
- "How does connection pooling work under the hood?" → `create_async_engine`
  maintains a pool of already-established connections; checking out a
  session borrows one, returns it to the pool on session close rather than
  closing the TCP connection — avoiding per-request TCP+auth handshake
  cost.
- "Why Alembic instead of `Base.metadata.create_all()`?" → `create_all` has
  no notion of incremental change or rollback; Alembic tracks schema
  history as versioned, reversible migrations — necessary the moment more
  than one person or environment touches the same schema.
- "How does theme persistence avoid a flash of wrong theme on reload?" →
  the next-themes pre-hydration script above.
- "Why layered folders now vs. feature folders?" → tradeoffs above; be
  ready to say when you'd switch (multiple domains, files that change
  together spanning folders).

## Common mistakes / misconceptions to avoid saying out loud

- Confusing "async" with "faster per-request" — it's about concurrency
  (handling more requests at once), not making any single request faster.
- Saying SQLAlchemy sessions (or engines/connection pools) are safely
  shareable across event loops or async tasks — they are not; one
  session per request/task is the rule, and a pooled engine is tied to
  the loop it was created on.
- Treating Alembic's autogenerate as infallible — it diffs metadata vs. DB
  state and misses some changes (renames look like drop+add); migrations
  should always be reviewed before applying.
