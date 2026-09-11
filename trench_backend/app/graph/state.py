"""LangGraph state for a single chat turn.

Kept as plain JSON-serializable data (strings, lists of dicts) rather than
ORM objects or callables -- everything here is written to the Postgres
checkpointer on every node transition, so it must serialize cleanly.
"""

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    user_id: str
    thread_id: str
    stream_id: str
    query: str
    knowledge_type: str
    # Populated by validate_knowledge_access.
    organization_id: str | None
    access_denied: bool
    denial_reason: str | None
    # The query actually used for retrieval -- the raw query, or an
    # LLM-condensed standalone rewrite when prior turns exist.
    condensed_query: str
    retrieved_chunks: list[dict[str, Any]]
    response: str
    citations: list[dict[str, Any]]
    messages: Annotated[list, add_messages]
