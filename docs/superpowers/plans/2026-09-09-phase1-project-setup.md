# Trench Phase 1: Project Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a working, verified skeleton of Trench — FastAPI backend with async SQLAlchemy/PostgreSQL and one real health endpoint, and a Next.js frontend with a working light/dark theme and a page that displays live backend health — with git initialized and secrets excluded.

**Architecture:** Backend is a layered FastAPI app (`api/`, `core/`, `db/`, `models/`, `schemas/`) managed by `uv`, using async SQLAlchemy 2.0 + asyncpg against the local `trench` PostgreSQL database, with Alembic wired for migrations from the start. Frontend is Next.js 16 (App Router) + React 19 with the full Abyss toolkit (Tailwind v4, shadcn/radix-ui, next-themes, zustand, react-query, axios, react-hook-form+zod), theming derived from the reference palette. The two communicate over HTTP; the frontend's landing page proves the whole chain by rendering the backend's live DB-connected health status.

**Tech Stack:** Python (latest stable, via uv), FastAPI, SQLAlchemy 2.0 (async) + asyncpg, Alembic, pydantic-settings, pytest + pytest-asyncio + httpx; Next.js 16, React 19, TypeScript, Tailwind v4, shadcn/radix-ui, next-themes, zustand, @tanstack/react-query, axios, react-hook-form + zod.

**Spec:** `docs/superpowers/specs/2026-09-09-phase1-project-setup-design.md`

## Global Constraints

- Python: latest stable (3.13+), managed by `uv` — no pip/venv/poetry.
- Database: local PostgreSQL, host `localhost`, db `trench`, user `postgres`, password `password` (already running; `trench` DB does not exist yet and must be created).
- Backend layout is layered-by-concern: `app/api/`, `app/core/`, `app/db/`, `app/models/`, `app/schemas/`.
- Frontend: Next.js 16 App Router, React 19, TypeScript, `src/` directory, Tailwind v4.
- Frontend adopts the full Abyss toolkit now: shadcn/radix-ui, next-themes, zustand, react-query, axios, react-hook-form + zod.
- Theme palette (from `frontend.html`): lime `#D3FB52`, muted ink `#273F43`, deep ink `#052326`, soft mint `#C7FBEA`, light aqua `#D3FBF6`, pale green `#EDFCD6`. Must support light + dark themes with a persisted preference.
- `frontend.html` and `trench-e4b2f-firebase-adminsdk-fbsvc-acc908edf9.json` are reference-only and must NEVER be committed — excluded by exact filename in `.gitignore`.
- No Firebase auth, no RAG-specific code/folders in this phase — strictly setup and wiring.

---

## File Structure

```
Trench/
  .gitignore
  trench_backend/
    pyproject.toml
    .env                      # gitignored, real local values
    .env.example              # committed, placeholder values
    alembic.ini
    alembic/
      env.py
      script.py.mako
      versions/
        <timestamp>_initial_empty_revision.py
    app/
      __init__.py
      main.py
      core/
        __init__.py
        config.py
        logging.py
      db/
        __init__.py
        base.py
        session.py
      api/
        __init__.py
        health.py
      models/
        __init__.py
      schemas/
        __init__.py
        health.py
    tests/
      __init__.py
      conftest.py
      test_health.py
  trench_frontend/
    package.json
    src/
      app/
        layout.tsx
        page.tsx
        settings/
          page.tsx
        globals.css
      components/
        theme-provider.tsx
        theme-toggle.tsx
        providers.tsx
      lib/
        api.ts
  docs/superpowers/
    specs/2026-09-09-phase1-project-setup-design.md
    plans/2026-09-09-phase1-project-setup.md
    phase1-setup-notes.md        # produced in Task 12
```

---

### Task 1: Backend project init with uv, dependencies, and database creation

**Files:**
- Create: `trench_backend/pyproject.toml` (via `uv init`)
- Create: `trench_backend/.python-version`

**Interfaces:**
- Produces: a `trench_backend/` uv project with FastAPI, SQLAlchemy async, asyncpg, pydantic-settings, alembic, uvicorn as runtime deps, and pytest, pytest-asyncio, httpx as dev deps. All later tasks run commands via `uv run` from `trench_backend/`.

- [ ] **Step 1: Create the local database**

Run:
```bash
psql -h localhost -U postgres -c "CREATE DATABASE trench;"
```
Expected: `CREATE DATABASE` (password prompt only if peer auth isn't configured — password is `password`).

- [ ] **Step 2: Init the uv project**

Run:
```bash
cd /home/ib-80/Desktop/Trench
uv init trench_backend --python 3.13 --no-workspace
```
Expected: `trench_backend/pyproject.toml`, `trench_backend/.python-version`, `trench_backend/main.py` (placeholder, will be replaced), `trench_backend/README.md` created.

- [ ] **Step 3: Remove the scaffold placeholder file**

Run:
```bash
rm /home/ib-80/Desktop/Trench/trench_backend/main.py
```

- [ ] **Step 4: Add runtime dependencies**

Run (from `trench_backend/`):
```bash
uv add fastapi "uvicorn[standard]" "sqlalchemy[asyncio]>=2.0" asyncpg pydantic-settings alembic
```
Expected: `pyproject.toml` `[project.dependencies]` updated, `uv.lock` created/updated.

- [ ] **Step 5: Add dev dependencies**

Run (from `trench_backend/`):
```bash
uv add --dev pytest pytest-asyncio httpx
```

- [ ] **Step 6: Verify the environment resolves**

Run:
```bash
uv run python -c "import fastapi, sqlalchemy, asyncpg, pydantic_settings, alembic; print('ok')"
```
Expected: prints `ok`.

- [ ] **Step 7: Commit**

```bash
cd /home/ib-80/Desktop/Trench
git init 2>/dev/null || true
git add trench_backend/pyproject.toml trench_backend/uv.lock trench_backend/.python-version trench_backend/README.md
git commit -m "chore: init trench_backend uv project with core dependencies"
```
(Full `.gitignore` lands in Task 5 — this early commit is fine since no secrets exist yet in tracked files.)

---

### Task 2: Core settings and logging

**Files:**
- Create: `trench_backend/app/__init__.py` (empty)
- Create: `trench_backend/app/core/__init__.py` (empty)
- Create: `trench_backend/app/core/config.py`
- Create: `trench_backend/app/core/logging.py`
- Create: `trench_backend/.env`
- Create: `trench_backend/.env.example`
- Test: `trench_backend/tests/__init__.py` (empty), `trench_backend/tests/test_config.py`

**Interfaces:**
- Produces: `get_settings() -> Settings` where `Settings` has fields `app_name: str`, `environment: str`, `database_url: str`, `log_level: str`. Later tasks (`db/session.py`, `main.py`) import `get_settings` from `app.core.config`.
- Produces: `configure_logging() -> None` from `app.core.logging`, called once in `app/main.py`.

- [ ] **Step 1: Create package init files**

```bash
mkdir -p /home/ib-80/Desktop/Trench/trench_backend/app/core
touch /home/ib-80/Desktop/Trench/trench_backend/app/__init__.py
touch /home/ib-80/Desktop/Trench/trench_backend/app/core/__init__.py
mkdir -p /home/ib-80/Desktop/Trench/trench_backend/tests
touch /home/ib-80/Desktop/Trench/trench_backend/tests/__init__.py
```

- [ ] **Step 2: Write `.env` and `.env.example`**

`trench_backend/.env`:
```
APP_NAME=Trench
ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/trench
LOG_LEVEL=INFO
```

`trench_backend/.env.example`:
```
APP_NAME=Trench
ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/trench_dev
LOG_LEVEL=INFO
```

- [ ] **Step 3: Write the failing test**

`trench_backend/tests/test_config.py`:
```python
from app.core.config import get_settings


def test_settings_load_database_url_from_env():
    settings = get_settings()
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.app_name == "Trench"
```

- [ ] **Step 4: Run test to verify it fails**

Run (from `trench_backend/`): `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.config'`

- [ ] **Step 5: Implement `app/core/config.py`**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = "Trench"
    environment: str = "development"
    database_url: str
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 6: Implement `app/core/logging.py`**

```python
import logging
import sys

from app.core.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stdout,
        force=True,
    )
```

- [ ] **Step 7: Run test to verify it passes**

Run (from `trench_backend/`): `uv run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd /home/ib-80/Desktop/Trench
git add trench_backend/app/__init__.py trench_backend/app/core trench_backend/tests/__init__.py trench_backend/tests/test_config.py trench_backend/.env.example
git commit -m "feat: add settings and logging config for trench_backend"
```
(`.env` is intentionally NOT added — it is gitignored starting Task 5; until then avoid staging it explicitly.)

---

### Task 3: Async DB session module and Alembic wiring

**Files:**
- Create: `trench_backend/app/db/__init__.py` (empty)
- Create: `trench_backend/app/db/base.py`
- Create: `trench_backend/app/db/session.py`
- Create: `trench_backend/alembic.ini`, `trench_backend/alembic/env.py`, `trench_backend/alembic/script.py.mako`, `trench_backend/alembic/versions/`
- Test: `trench_backend/tests/test_db_session.py`

**Interfaces:**
- Consumes: `get_settings` from `app.core.config` (Task 2).
- Produces: `Base` (SQLAlchemy `DeclarativeBase`) from `app.db.base`, used by future models and by Alembic's `target_metadata`. Produces `engine`, `async_session_factory`, and `get_db() -> AsyncGenerator[AsyncSession, None]` from `app.db.session`, consumed by the health endpoint (Task 4) as a FastAPI dependency.

- [ ] **Step 1: Create the db package**

```bash
mkdir -p /home/ib-80/Desktop/Trench/trench_backend/app/db
touch /home/ib-80/Desktop/Trench/trench_backend/app/db/__init__.py
```

- [ ] **Step 2: Implement `app/db/base.py`**

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [ ] **Step 3: Implement `app/db/session.py`**

```python
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
```

- [ ] **Step 4: Write the failing test**

`trench_backend/tests/test_db_session.py`:
```python
import pytest
from sqlalchemy import text

from app.db.session import async_session_factory


@pytest.mark.asyncio
async def test_session_can_execute_select_1():
    async with async_session_factory() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
```

- [ ] **Step 5: Configure pytest-asyncio mode**

Add to `trench_backend/pyproject.toml` under a new `[tool.pytest.ini_options]` section:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 6: Run test to verify it fails**

Run (from `trench_backend/`): `uv run pytest tests/test_db_session.py -v`
Expected: FAIL initially with `ModuleNotFoundError` if run before Step 2/3 exist — since they now exist, instead run before Step 5's config to confirm it fails without asyncio_mode set (async def test collected as a warning/error). If it already passes because pytest-asyncio auto-detects, note that and proceed — the goal is confirming DB connectivity, not manufacturing a false-negative.

- [ ] **Step 7: Run test to verify it passes**

Run (from `trench_backend/`): `uv run pytest tests/test_db_session.py -v`
Expected: PASS — proves the async engine connects to the real local `trench` database.

- [ ] **Step 8: Initialize Alembic with the async template**

Run (from `trench_backend/`):
```bash
uv run alembic init -t async alembic
```
Expected: creates `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/`.

- [ ] **Step 9: Wire Alembic to app settings and metadata**

Edit `trench_backend/alembic/env.py`:
- Add near the top (after existing imports):
```python
from app.core.config import get_settings
from app.db.base import Base
```
- Replace the line `target_metadata = None` with:
```python
target_metadata = Base.metadata
```
- Inside `run_migrations_offline()` and `run_migrations_online()` (or immediately after `config = context.config`), add:
```python
config.set_main_option("sqlalchemy.url", get_settings().database_url)
```

- [ ] **Step 10: Generate and apply the initial (empty) migration**

Run (from `trench_backend/`):
```bash
uv run alembic revision --autogenerate -m "initial empty revision"
uv run alembic upgrade head
```
Expected: a revision file appears in `alembic/versions/`; `alembic upgrade head` reports success and creates the `alembic_version` table in the `trench` database.

- [ ] **Step 11: Verify migration applied**

Run:
```bash
psql -h localhost -U postgres -d trench -c "\dt"
```
Expected: `alembic_version` table listed.

- [ ] **Step 12: Commit**

```bash
cd /home/ib-80/Desktop/Trench
git add trench_backend/app/db trench_backend/tests/test_db_session.py trench_backend/pyproject.toml trench_backend/alembic.ini trench_backend/alembic
git commit -m "feat: add async DB session module and wire Alembic migrations"
```

---

### Task 4: Health endpoint and FastAPI app

**Files:**
- Create: `trench_backend/app/schemas/__init__.py` (empty)
- Create: `trench_backend/app/schemas/health.py`
- Create: `trench_backend/app/api/__init__.py` (empty)
- Create: `trench_backend/app/api/health.py`
- Create: `trench_backend/app/main.py`
- Test: `trench_backend/tests/conftest.py`, `trench_backend/tests/test_health.py`

**Interfaces:**
- Consumes: `get_db` from `app.db.session` (Task 3).
- Produces: FastAPI `app` instance in `app.main`, exposing `GET /health` that returns `{"status": "ok", "db": "connected"}`. Later, the frontend's `lib/api.ts` (Task 8) calls this exact path and shape.

- [ ] **Step 1: Create schema/api packages**

```bash
mkdir -p /home/ib-80/Desktop/Trench/trench_backend/app/schemas /home/ib-80/Desktop/Trench/trench_backend/app/api
touch /home/ib-80/Desktop/Trench/trench_backend/app/schemas/__init__.py
touch /home/ib-80/Desktop/Trench/trench_backend/app/api/__init__.py
```

- [ ] **Step 2: Write the failing test**

`trench_backend/tests/conftest.py`:
```python
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

`trench_backend/tests/test_health.py`:
```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check_returns_ok_and_db_connected(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "connected"}
```

- [ ] **Step 3: Run test to verify it fails**

Run (from `trench_backend/`): `uv run pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 4: Implement `app/schemas/health.py`**

```python
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    db: str
```

- [ ] **Step 5: Implement `app/api/health.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    await db.execute(text("SELECT 1"))
    return HealthResponse(status="ok", db="connected")
```

- [ ] **Step 6: Implement `app/main.py`**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.core.logging import configure_logging

configure_logging()

app = FastAPI(title="Trench API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
```

- [ ] **Step 7: Run test to verify it passes**

Run (from `trench_backend/`): `uv run pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 8: Run the full backend test suite**

Run (from `trench_backend/`): `uv run pytest -v`
Expected: all tests (`test_config`, `test_db_session`, `test_health`) PASS.

- [ ] **Step 9: Smoke-test the live server**

Run (from `trench_backend/`, in one terminal): `uv run uvicorn app.main:app --reload --port 8000`
In another shell: `curl -s http://localhost:8000/health`
Expected: `{"status":"ok","db":"connected"}`. Stop the server after confirming.

- [ ] **Step 10: Commit**

```bash
cd /home/ib-80/Desktop/Trench
git add trench_backend/app/schemas trench_backend/app/api trench_backend/app/main.py trench_backend/tests/conftest.py trench_backend/tests/test_health.py
git commit -m "feat: add /health endpoint with DB connectivity check"
```

---

### Task 5: Git repo hygiene — full `.gitignore`

**Files:**
- Create: `/home/ib-80/Desktop/Trench/.gitignore`

**Interfaces:**
- Produces: repo-root `.gitignore` that all subsequent frontend commits rely on to keep `node_modules`, `.next`, and secrets untracked.

- [ ] **Step 1: Write the root `.gitignore`**

`/home/ib-80/Desktop/Trench/.gitignore`:
```
# Reference-only files — never commit
frontend.html
trench-e4b2f-firebase-adminsdk-fbsvc-acc908edf9.json

# Python
__pycache__/
*.pyc
.venv/
trench_backend/.env
.pytest_cache/

# Node / Next.js
node_modules/
trench_frontend/.next/
trench_frontend/out/
trench_frontend/.env.local
*.tsbuildinfo

# Editors / OS
.DS_Store
.vscode/
```

- [ ] **Step 2: Verify the ignored files are untracked**

Run:
```bash
cd /home/ib-80/Desktop/Trench
git status --porcelain | grep -E "frontend\.html|firebase-adminsdk|\.env$" || echo "CLEAN"
```
Expected: `CLEAN`.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: add root .gitignore excluding secrets and reference files"
```

---

### Task 6: Frontend scaffold — Next.js 16 + React 19 + TypeScript + Tailwind v4

**Files:**
- Create: `trench_frontend/` (entire scaffold via `create-next-app`)

**Interfaces:**
- Produces: a runnable Next.js app at `trench_frontend/` with `src/app/` App Router, TypeScript, Tailwind v4 configured, ESLint configured. Later tasks add theming, state libs, and pages inside `src/`.

- [ ] **Step 1: Scaffold the app**

Run (from `/home/ib-80/Desktop/Trench`):
```bash
npx --yes create-next-app@latest trench_frontend \
  --typescript --tailwind --eslint --app --src-dir \
  --import-alias "@/*" --use-npm --no-turbopack --yes
```
Expected: `trench_frontend/` created with `src/app/page.tsx`, `src/app/layout.tsx`, `src/app/globals.css`, `package.json`, `tsconfig.json`.

- [ ] **Step 2: Verify the default app runs**

Run (from `trench_frontend/`): `npm run dev -- --port 3000 &` then `curl -s http://localhost:3000 | grep -o "<title>[^<]*</title>"`
Expected: prints a `<title>` tag (default Next.js title), confirming the dev server serves pages. Stop the dev server after confirming (`kill %1` or equivalent).

- [ ] **Step 3: Commit**

```bash
cd /home/ib-80/Desktop/Trench
git add trench_frontend
git commit -m "chore: scaffold trench_frontend with Next.js 16, TypeScript, Tailwind v4"
```

---

### Task 7: Theme system — palette tokens, next-themes, shadcn init

**Files:**
- Modify: `trench_frontend/src/app/globals.css`
- Modify: `trench_frontend/src/app/layout.tsx`
- Create: `trench_frontend/src/components/theme-provider.tsx`
- Create: `trench_frontend/components.json` (via shadcn init)

**Interfaces:**
- Consumes: nothing new.
- Produces: `<ThemeProvider>` component from `@/components/theme-provider`, wrapping the app in `layout.tsx`; CSS custom properties (`--background`, `--foreground`, `--primary`, etc.) defined for both `:root` (light) and `.dark` (dark), derived from the Trench palette. Task 10's theme toggle depends on `next-themes`' `useTheme()` being available, which requires this provider.

- [ ] **Step 1: Install next-themes**

Run (from `trench_frontend/`): `npm install next-themes`

- [ ] **Step 2: Initialize shadcn**

Run (from `trench_frontend/`): `npx --yes shadcn@latest init -d`
Expected: creates `components.json`, adds `src/lib/utils.ts`, updates `globals.css` with base shadcn CSS variables and `tailwindcss-animate`-equivalent config for Tailwind v4.

- [ ] **Step 3: Add the button and switch components (needed by Task 10)**

Run (from `trench_frontend/`): `npx --yes shadcn@latest add button switch`
Expected: `src/components/ui/button.tsx` and `src/components/ui/switch.tsx` created.

- [ ] **Step 4: Override CSS variables with the Trench palette**

Edit `trench_frontend/src/app/globals.css` — replace the `:root { ... }` and `.dark { ... }` blocks shadcn generated with:
```css
:root {
  --background: #EDFCD6;
  --foreground: #052326;
  --card: #ffffff;
  --card-foreground: #052326;
  --primary: #D3FB52;
  --primary-foreground: #052326;
  --secondary: #C7FBEA;
  --secondary-foreground: #052326;
  --muted: #D3FBF6;
  --muted-foreground: #273F43;
  --accent: #C7FBEA;
  --accent-foreground: #052326;
  --border: rgba(5, 35, 38, 0.12);
  --input: rgba(5, 35, 38, 0.12);
  --ring: #D3FB52;
  --radius: 0.75rem;
}

.dark {
  --background: #052326;
  --foreground: #D3FBF6;
  --card: #0b3438;
  --card-foreground: #D3FBF6;
  --primary: #D3FB52;
  --primary-foreground: #052326;
  --secondary: #273F43;
  --secondary-foreground: #D3FBF6;
  --muted: #273F43;
  --muted-foreground: #C7FBEA;
  --accent: #273F43;
  --accent-foreground: #D3FBF6;
  --border: rgba(211, 251, 246, 0.14);
  --input: rgba(211, 251, 246, 0.14);
  --ring: #D3FB52;
  --radius: 0.75rem;
}
```
(Keep any `@theme inline` Tailwind v4 mapping block that shadcn generated above these — it maps `--color-background: var(--background)` etc.; only the variable values change, not the mapping.)

- [ ] **Step 5: Implement the theme provider**

`trench_frontend/src/components/theme-provider.tsx`:
```tsx
"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

export function ThemeProvider({
  children,
  ...props
}: ComponentProps<typeof NextThemesProvider>) {
  return <NextThemesProvider {...props}>{children}</NextThemesProvider>;
}
```

- [ ] **Step 6: Wrap the app in `layout.tsx`**

Edit `trench_frontend/src/app/layout.tsx` to wrap `{children}` with the provider:
```tsx
import { ThemeProvider } from "@/components/theme-provider";

// ...inside the body, replace `{children}` with:
<ThemeProvider attribute="class" defaultTheme="system" enableSystem>
  {children}
</ThemeProvider>
```
Keep the existing `<html lang="en" suppressHydrationWarning>` attribute — `suppressHydrationWarning` is required on `<html>` when using next-themes' class attribute strategy to avoid a hydration mismatch warning.

- [ ] **Step 7: Verify it builds**

Run (from `trench_frontend/`): `npm run build`
Expected: build succeeds with no type errors.

- [ ] **Step 8: Commit**

```bash
cd /home/ib-80/Desktop/Trench
git add trench_frontend/package.json trench_frontend/package-lock.json trench_frontend/components.json trench_frontend/src
git commit -m "feat: wire next-themes and Trench palette tokens via shadcn"
```

---

### Task 8: State/data libraries and the backend API client

**Files:**
- Create: `trench_frontend/src/components/providers.tsx`
- Create: `trench_frontend/src/lib/api.ts`
- Modify: `trench_frontend/src/app/layout.tsx`

**Interfaces:**
- Consumes: backend `GET /health` from Task 4, returning `{status: string, db: string}`.
- Produces: `getHealth(): Promise<HealthResponse>` from `@/lib/api`, and a `<Providers>` component wrapping `<QueryClientProvider>`, consumed by Task 9's landing page via `useQuery`.

- [ ] **Step 1: Install dependencies**

Run (from `trench_frontend/`):
```bash
npm install zustand @tanstack/react-query axios react-hook-form @hookform/resolvers zod
```

- [ ] **Step 2: Implement the API client**

`trench_frontend/src/lib/api.ts`:
```ts
import axios from "axios";

export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
});

export interface HealthResponse {
  status: string;
  db: string;
}

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>("/health");
  return data;
}
```

- [ ] **Step 3: Add the env var**

Create `trench_frontend/.env.local`:
```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```
(This file is already covered by the root `.gitignore` from Task 5.)

- [ ] **Step 4: Implement the providers wrapper**

`trench_frontend/src/components/providers.tsx`:
```tsx
"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { ThemeProvider } from "@/components/theme-provider";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 5: Update `layout.tsx` to use the combined `Providers`**

Edit `trench_frontend/src/app/layout.tsx`: replace the direct `<ThemeProvider>` usage from Task 7 Step 6 with `<Providers>{children}</Providers>`, and remove the now-redundant `ThemeProvider` import (it's used inside `providers.tsx` instead).

- [ ] **Step 6: Verify it builds**

Run (from `trench_frontend/`): `npm run build`
Expected: succeeds with no type errors.

- [ ] **Step 7: Commit**

```bash
cd /home/ib-80/Desktop/Trench
git add trench_frontend/package.json trench_frontend/package-lock.json trench_frontend/src/lib trench_frontend/src/components/providers.tsx trench_frontend/src/app/layout.tsx
git commit -m "feat: add react-query, zustand, axios client, and combined providers"
```

---

### Task 9: Landing page — live backend health display

**Files:**
- Modify: `trench_frontend/src/app/page.tsx`

**Interfaces:**
- Consumes: `getHealth` from `@/lib/api` (Task 8).

- [ ] **Step 1: Implement the landing page**

Replace the contents of `trench_frontend/src/app/page.tsx`:
```tsx
"use client";

import { useQuery } from "@tanstack/react-query";

import { getHealth } from "@/lib/api";

export default function Home() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background text-foreground">
      <h1 className="text-3xl font-bold">Trench</h1>
      {isLoading && <p>Checking backend status…</p>}
      {isError && <p className="text-red-500">Backend unreachable</p>}
      {data && (
        <p>
          Backend status: <strong>{data.status}</strong> · DB:{" "}
          <strong>{data.db}</strong>
        </p>
      )}
    </main>
  );
}
```

- [ ] **Step 2: Manual verification with both servers running**

Terminal 1 (from `trench_backend/`): `uv run uvicorn app.main:app --reload --port 8000`
Terminal 2 (from `trench_frontend/`): `npm run dev -- --port 3000`
Then: `curl -s http://localhost:3000 | grep -o "Backend status[^<]*"` (or open `http://localhost:3000` in a browser)
Expected: page renders "Backend status: ok · DB: connected". Stop both servers after confirming.

- [ ] **Step 3: Commit**

```bash
cd /home/ib-80/Desktop/Trench
git add trench_frontend/src/app/page.tsx
git commit -m "feat: landing page displays live backend health status"
```

---

### Task 10: Settings page with theme toggle

**Files:**
- Create: `trench_frontend/src/app/settings/page.tsx`
- Create: `trench_frontend/src/components/theme-toggle.tsx`

**Interfaces:**
- Consumes: `useTheme` from `next-themes` (via the provider wired in Task 7/8), `Switch` and `Button` from `@/components/ui/*` (Task 7 Step 3).

- [ ] **Step 1: Implement the theme toggle component**

`trench_frontend/src/components/theme-toggle.tsx`:
```tsx
"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";

import { Switch } from "@/components/ui/switch";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  if (!mounted) return null;

  const isDark = theme === "dark";

  return (
    <div className="flex items-center gap-3">
      <span>Light</span>
      <Switch
        checked={isDark}
        onCheckedChange={(checked) => setTheme(checked ? "dark" : "light")}
        aria-label="Toggle dark mode"
      />
      <span>Dark</span>
    </div>
  );
}
```
(The `mounted` guard prevents a hydration mismatch: `next-themes` can't know the persisted theme during server render, so we render nothing until the client mounts.)

- [ ] **Step 2: Implement the settings page**

`trench_frontend/src/app/settings/page.tsx`:
```tsx
import { ThemeToggle } from "@/components/theme-toggle";

export default function SettingsPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background text-foreground">
      <h1 className="text-2xl font-bold">Settings</h1>
      <section className="flex flex-col items-center gap-2">
        <h2 className="text-sm uppercase tracking-wide text-muted-foreground">
          Theme
        </h2>
        <ThemeToggle />
      </section>
    </main>
  );
}
```

- [ ] **Step 3: Manual verification**

With `npm run dev -- --port 3000` running, open `http://localhost:3000/settings`, toggle the switch, confirm the background/foreground colors swap between the light and dark token sets, reload the page, and confirm the chosen theme persists (next-themes persists to `localStorage` under the key `theme` by default).

- [ ] **Step 4: Commit**

```bash
cd /home/ib-80/Desktop/Trench
git add trench_frontend/src/app/settings trench_frontend/src/components/theme-toggle.tsx
git commit -m "feat: add settings page with persisted light/dark theme toggle"
```

---

### Task 11: Full-stack integration verification

**Files:** none (verification only)

- [ ] **Step 1: Run the backend test suite**

Run (from `trench_backend/`): `uv run pytest -v`
Expected: all tests pass.

- [ ] **Step 2: Run the frontend production build**

Run (from `trench_frontend/`): `npm run build`
Expected: build succeeds.

- [ ] **Step 3: Run both servers together and verify end-to-end**

Terminal 1 (from `trench_backend/`): `uv run uvicorn app.main:app --port 8000`
Terminal 2 (from `trench_frontend/`): `npm run start -- --port 3000` (after `npm run build`)
Then:
```bash
curl -s http://localhost:8000/health
curl -s http://localhost:3000 | grep -o "Backend status[^<]*"
```
Expected: backend returns `{"status":"ok","db":"connected"}`; frontend page contains the rendered status string. Stop both servers after confirming.

- [ ] **Step 4: Confirm secrets are not tracked**

Run:
```bash
cd /home/ib-80/Desktop/Trench
git ls-files | grep -E "frontend\.html|firebase-adminsdk|\.env$|\.env\.local$" || echo "CLEAN"
```
Expected: `CLEAN`.

- [ ] **Step 5: Commit any remaining untracked scaffold files (e.g. `.next` build artifacts must NOT be added)**

```bash
git status --porcelain
```
Review output; stage and commit only source files that legitimately haven't been committed yet (there should be none if prior tasks committed correctly). If clean, no commit needed.

---

### Task 12: Phase 1 documentation for interview prep

**Files:**
- Create: `docs/superpowers/phase1-setup-notes.md`

- [ ] **Step 1: Write the documentation**

`docs/superpowers/phase1-setup-notes.md`:
```markdown
# Trench Phase 1 — Setup: Implementation Notes & Interview Prep

## What was built

A FastAPI backend (async SQLAlchemy + PostgreSQL, Alembic-managed) exposing
one real endpoint, `GET /health`, which proves DB connectivity by running
`SELECT 1` through the async session. A Next.js 16 frontend (App Router,
React 19) renders that status live on its landing page and provides a
`/settings` page with a persisted light/dark theme toggle built on
next-themes and shadcn/radix-ui primitives, themed from a custom palette.

## Request flow

1. Browser loads `/` → React Query's `useQuery` calls `getHealth()`
   (`src/lib/api.ts`), which issues `axios.get("/health")` against the
   FastAPI base URL.
2. FastAPI's `health_check` handler receives the request, and its
   `db: AsyncSession = Depends(get_db)` dependency triggers `get_db()`
   (`app/db/session.py`), which opens a session from
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
installs — comparable to what `package-lock.json` does for npm. It's also
increasingly the standard tool in modern Python shops, which is itself an
interview-relevant signal.

## Why this backend layout (layered-by-concern)

At this size (one endpoint), a layered structure (`api/`, `core/`, `db/`,
`models/`, `schemas/`) keeps each file's responsibility obvious: routers
never touch settings directly, models never import routers, etc. This
isn't the final word — once Trench grows multiple domains (documents,
chat, agents), a feature-based layout (each domain owns its own
router+model+schema) usually wins because it keeps files that change
together in one place. The plan explicitly deferred that restructuring
until there's a second domain to justify it — premature feature folders
would just be empty scaffolding.

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
intentional mismatch.

## Alembic — why wire it up now, with zero models

Migrations are a habit, not a checkpoint you add later. Wiring Alembic
against an empty `Base.metadata` now means the *first* migration for
Phase 2 (a `users` or `sessions` table for Firebase-linked profiles, most
likely) is a normal autogenerate diff against a known-working pipeline,
not a from-scratch setup done under feature pressure.

## Interview questions this phase prepares you for

- "Walk me through what happens between a browser request and a DB query
  in your FastAPI app." → the request flow above.
- "Why async over sync SQLAlchemy? What are the gotchas?" → event loop
  blocking; sessions aren't safely shared across concurrent tasks; the
  `pool_pre_ping=True` detail (why: recycles dead connections after DB
  restarts/idle timeouts, at the cost of one extra round-trip per
  checkout).
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
- Saying SQLAlchemy sessions are thread-safe or safely shareable across
  async tasks — they are not; one session per request/task is the rule.
- Treating Alembic's autogenerate as infallible — it diffs metadata vs. DB
  state and misses some changes (renames look like drop+add); migrations
  should always be reviewed before applying.
```

- [ ] **Step 2: Commit**

```bash
cd /home/ib-80/Desktop/Trench
git add docs/superpowers/phase1-setup-notes.md
git commit -m "docs: add Phase 1 setup implementation notes and interview prep"
```
