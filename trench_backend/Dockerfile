# syntax=docker/dockerfile:1
# Trench backend: FastAPI + LangGraph on Python 3.13, dependencies managed by uv.
# Two stages: "builder" installs dependencies, "runtime" is the slim final image.

# ============================================================================
# Stage 1: builder (installs dependencies into /app/.venv)
# ============================================================================
FROM python:3.13-slim AS builder

# Copy the uv binary from its official image (pinned to the version you use
# locally), so we don't need pip or curl to install it.
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

# UV_COMPILE_BYTECODE : precompile .pyc files, so the app starts faster
# UV_LINK_MODE=copy   : copy files out of the cache (no hard links), so the
#                       venv is self-contained when copied to stage 2
# UV_PYTHON_DOWNLOADS : use the image's Python 3.13; never download another
# TIKTOKEN_CACHE_DIR  : where tiktoken saves its downloaded data files
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    TIKTOKEN_CACHE_DIR=/app/.tiktoken

WORKDIR /app

# Dependency layer. Only the two lockfiles are copied here, so this slow
# step is cached and re-runs only when dependencies actually change.
#   --frozen               : install exactly what uv.lock says; fail if outdated
#   --no-dev               : skip pytest, ruff and other dev tools
#   --no-install-project   : the backend isn't an installable package
#                            (pyproject.toml has no build section)
# The cache mount keeps uv's download cache between builds but outside the image.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Download tiktoken's encoding files now, at build time, so the running
# container never needs to fetch them:
#   cl100k_base -> used for chunking (loaded at app startup)
#   o200k_base  -> used to size Groq output limits
RUN /app/.venv/bin/python -c "import tiktoken; [tiktoken.get_encoding(n) for n in ('cl100k_base', 'o200k_base')]"

# Download the NLTK data llama-index-core (used by llama-cloud-services for
# document parsing) needs:
#   punkt_tab -> sentence splitting
#   stopwords -> common words to ignore
# It reads them from NLTK_DATA (set in the runtime stage). Without this it
# tries to download them at runtime into its own package folder, which the
# non-root "app" user can't write to.
RUN /app/.venv/bin/python -c "import nltk; [nltk.download(p, download_dir='/app/nltk_data', quiet=True, raise_on_error=True) for p in ('punkt_tab', 'stopwords')]"

# ============================================================================
# Stage 2: runtime (what actually runs; no uv, no build caches)
# ============================================================================
FROM python:3.13-slim AS runtime

# PATH                    : use the venv's python and uvicorn by default
# PYTHONUNBUFFERED        : print logs immediately (no buffering), so
#                           `docker logs` is real-time
# PYTHONDONTWRITEBYTECODE : don't try to write .pyc files (the code folder is
#                           read-only for the app user)
# TIKTOKEN_CACHE_DIR      : read the files downloaded in stage 1
# NLTK_DATA               : where NLTK finds punkt_tab + stopwords (stage 1)
# PORT                    : port to listen on. Render overrides this.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TIKTOKEN_CACHE_DIR=/app/.tiktoken \
    NLTK_DATA=/app/nltk_data \
    PORT=8000

# Security hardening (the CI Trivy scan gates on fixable CRITICAL/HIGH CVEs):
# - apply Debian security updates published since the base image was built
# - remove pip: the app never installs anything at runtime (the venv is
#   complete), and pip bundles its own old copies of urllib3/msgpack/
#   setuptools that would otherwise ship in the image
# Then create an unprivileged system user "app" (no password, no home folder).
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall -y pip \
 && groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

# Bring over only the results from the builder: the venv and the
# pre-downloaded tiktoken + NLTK data.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/.tiktoken /app/.tiktoken
COPY --from=builder /app/nltk_data /app/nltk_data

# Application code. It changes most often, so it's copied last for the best
# cache reuse. alembic + scripts are included so migrations and the provider
# seed script can be run with `docker exec` or as a one-off container later.
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY app ./app

# From here on, everything runs as "app", not root.
USER app

# Documentation of the port the app listens on (it doesn't publish it;
# `docker run -p` does that).
EXPOSE 8000

# Docker checks /health every 30 s. /health also checks the database, so
# "healthy" means app and DB are both OK. The image has no curl, so it uses
# Python's built-in urllib. Wait 40 s after start before counting failures.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"]

# Start the server.
#   sh -c           : needed so ${PORT} is filled in from the environment
#   exec            : uvicorn replaces the shell and becomes process 1, so it
#                     receives Docker's stop signal and shuts down cleanly
#   --host 0.0.0.0  : listen on all interfaces (127.0.0.1 would be
#                     unreachable from outside the container)
#   --proxy-headers / --forwarded-allow-ips : trust the HTTPS proxy in front
#                     (Render), so the app sees the real client scheme and IP
#   No --reload     : containers run fixed code; reload is for development
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
