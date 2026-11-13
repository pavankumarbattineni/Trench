"""The only place Trench reads from LangSmith (read-only).

Configuration is the same environment LangSmith tracing already uses (see
app/utils/tracing.py): LANGCHAIN_TRACING, LANGSMITH_API_KEY,
LANGCHAIN_PROJECT and LANGCHAIN_ENDPOINT. Every query is scoped to that one
project, so a key that can see other projects in the same LangSmith account
never exposes them here. Calls are synchronous (the SDK's client); callers
run them in a thread.

Uses `Client.list_runs` (LangSmith's /runs/query endpoint), supported until
January 2027; when moving to `client.runs.query`, only this file changes.
"""

import os
import warnings
from datetime import datetime
from functools import lru_cache

from langsmith import Client
from langsmith.schemas import Run


def _env(name: str) -> str:
    return (
        os.environ.get(f"LANGSMITH_{name}") or os.environ.get(f"LANGCHAIN_{name}") or ""
    ).strip()


def tracing_enabled() -> bool:
    return (
        _env("TRACING").lower() == "true"
        and bool(_env("API_KEY"))
        and bool(project_name())
    )


def project_name() -> str:
    return _env("PROJECT")


@lru_cache(maxsize=1)
def _client() -> Client:
    return Client(api_key=_env("API_KEY"), api_url=_env("ENDPOINT") or None)


def fetch_trace_runs(trace_id: str) -> list[Run]:
    """Every run of one trace, in Trench's project."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return list(_client().list_runs(project_name=project_name(), trace_id=trace_id))


def list_user_root_runs(user_id: str, since: datetime, limit: int = 100) -> list[Run]:
    """Root runs tagged with this user's id (metadata.user_id), newest first."""
    # user_id is a UUID from our own database; quotes can't appear in it.
    query = f'and(eq(metadata_key, "user_id"), eq(metadata_value, "{user_id}"))'
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return list(
            _client().list_runs(
                project_name=project_name(),
                is_root=True,
                start_time=since,
                filter=query,
                limit=limit,
            )
        )
