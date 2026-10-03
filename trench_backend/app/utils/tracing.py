"""LangSmith tracing configuration.

LangSmith/LangChain read their own config (`LANGSMITH_API_KEY`,
`LANGCHAIN_TRACING`, `LANGCHAIN_ENDPOINT`, `LANGCHAIN_PROJECT`) directly
from process environment variables -- not through TRENCH_CONFIG, which is
a single JSON blob parsed by pydantic-settings into typed fields, and
doesn't get exported to os.environ. These are plain KEY=VALUE lines in
the same .env file (see .env.example); `configure_tracing()` loads them
into the process environment so the LangGraph run (see app/graph/
rag_graph.py) gets traced automatically, with no separate wiring needed
at each LLM call site.

`langgraph dev` (the CLI) is a different process -- it loads env vars via
langgraph.json's own "env" key instead, pointed at the same .env file.
"""

from pathlib import Path

from dotenv import load_dotenv

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


def configure_tracing() -> None:
    load_dotenv(_ENV_FILE)
