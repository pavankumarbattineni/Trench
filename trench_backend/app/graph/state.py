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
    # LLM-condensed standalone rewrite when prior turns exist. Mutated by
    # reformulate_query on each retry.
    condensed_query: str
    # Set once by condense_query and never touched again -- what
    # condensed_query *started* this turn as, before any reformulate_
    # query rewrite. reformulate_query always rewrites from this, never
    # from condensed_query itself, so a second retry rewrites the
    # original standalone question again rather than compounding drift
    # onto an already-once-reformulated query (see app/graph/rag_graph.py).
    original_condensed_query: str
    retrieved_chunks: list[dict[str, Any]]
    # The highest-scoring attempt's retrieved_chunks across the retrieve/
    # reformulate retry loop, tracked by assess_retrieval_sufficiency --
    # generate() and build_citations() read this, not retrieved_chunks
    # directly, so a later retry that scores worse than an earlier one
    # never discards the better context (see app/graph/rag_graph.py).
    best_retrieved_chunks: list[dict[str, Any]]
    # Populated by assess_retrieval_sufficiency -- gates the
    # retrieve/reformulate retry loop, capped at MAX_RETRIEVAL_ATTEMPTS
    # (see app/graph/rag_graph.py).
    retrieval_sufficient: bool
    retrieval_attempts: int
    # The latest attempt's top rerank score (not best_retrieved_chunks'
    # score, which tracks the best attempt so far, not necessarily the
    # latest one) -- read by _route_after_sufficiency_check to skip
    # straight to generate() when even the latest attempt scored below
    # RETRIEVAL_SKIP_RETRY_BELOW, rather than spending a retry on a query
    # that almost certainly has nothing relevant to find.
    last_retrieval_top_score: float
    response: str
    citations: list[dict[str, Any]]
    messages: Annotated[list, add_messages]
