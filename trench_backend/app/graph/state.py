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
    # Populated by check_input_guardrail -- on by default for every turn
    # (architecture doc §10). input_guardrail_flagged distinguishes *why*
    # access_denied is set here from an authorization denial, even though
    # persist's status logic treats both as "the turn stopped early."
    input_guardrail_flagged: bool
    # Populated by check_output_guardrail -- every redaction reason, for
    # observability (see Task 5/6's tracing work); empty when nothing
    # was flagged.
    guardrail_flags: list[str]
    # Populated by validate_knowledge_access.
    tenant_id: str | None
    access_denied: bool
    denial_reason: str | None
    # Whether the resolved scope (personal for this user, or company for
    # this tenant) has zero fully-ingested documents -- as opposed to
    # having documents but none matching this query. Lets `generate` tell
    # the model the honest reason up front instead of it guessing from an
    # empty chunk list alone, and lets the graph skip the retrieve/
    # reformulate loop entirely when there is nothing to retrieve.
    knowledge_base_empty: bool
    # The query actually used for retrieval -- the raw query, or an
    # LLM-condensed standalone rewrite when prior turns exist.
    condensed_query: str
    retrieved_chunks: list[dict[str, Any]]
    # Populated by assess_retrieval_sufficiency -- gates the
    # retrieve/reformulate retry loop, capped at MAX_RETRIEVAL_ATTEMPTS
    # (see app/graph/rag_graph.py).
    retrieval_sufficient: bool
    retrieval_attempts: int
    response: str
    citations: list[dict[str, Any]]
    messages: Annotated[list, add_messages]
