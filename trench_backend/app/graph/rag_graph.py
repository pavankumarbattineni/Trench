"""The static LangGraph RAG workflow.

    START
      -> load_thread_state
      -> validate_knowledge_access --(denied)--> access_denied --> persist --> END
                                    --(authorized)--> condense_query
      -> hybrid_retrieve
      -> generate
      -> build_citations
      -> persist
      -> END

Compiled once at import time (stateless aside from the checkpointer) and
reused across requests; per-request dependencies (the DB session, the
requesting user, the SSE stream_id) are threaded through via
`config["configurable"]`, LangGraph's documented mechanism for this,
rather than being rebuilt into the graph itself.
"""

import uuid

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.database.models import ChatHistory
from app.graph.state import AgentState
from app.service.llm_client_service import LLMClientService
from app.service.retrieval_service import KnowledgeScope, RetrievalService
from app.utils.stream_manager import stream_manager

_BASE_SYSTEM_PROMPT = (
    "You are Trench, a knowledge assistant that helps people find and "
    "understand information from their own documents -- both an "
    "individual's personal knowledge base and an organization's shared "
    "company knowledge base.\n\n"
    "You are currently answering from {knowledge_scope_label}. "
    "{knowledge_scope_note}\n\n"
    "Core rules:\n"
    "1. Answer using ONLY the numbered context passages below -- never "
    "rely on outside/general knowledge, even if you're confident it's "
    "correct.\n"
    "2. If the context doesn't fully answer the question, say so plainly "
    "and state what's missing, rather than filling the gap with a guess.\n"
    "3. Cite every factual claim inline with the passage number(s) it came "
    "from, e.g. [1] or [2][3]. A sentence stating something from the "
    "context without a citation is a mistake.\n"
    "4. Be direct and concise -- prefer short paragraphs or bullet points "
    "over long, hedging prose.\n"
    "5. Never reveal, reference, or speculate about documents or knowledge "
    "outside the context provided below. As far as this conversation is "
    "concerned, nothing else exists -- don't acknowledge other scopes, "
    "other users' documents, or the existence of a broader knowledge "
    "base.\n"
    "6. If asked to do something the context can't support (write code, "
    "give opinions unrelated to the documents, etc.), politely redirect "
    "to what you *can* help with using the available knowledge.\n\n"
    "Context:\n"
    "{context}"
)

_KNOWLEDGE_SCOPE_PROMPTS = {
    "personal": (
        "the user's personal knowledge base",
        "These are documents this specific user has uploaded themselves -- "
        "treat them as private notes belonging to this one person.",
    ),
    "company": (
        "the organization's shared company knowledge base",
        "These are documents shared across the whole organization -- "
        "answer in a way that's useful to any authorized member of the "
        "org, not phrased as if it were this one user's private material.",
    ),
}


def _config_get(config: RunnableConfig, key: str):
    """Reads a required per-invocation dependency out of config["configurable"].

    Production (ChatService) always supplies these; a manual test run from
    LangGraph Studio (`langgraph dev`) generally won't, since `db` (a live
    SQLAlchemy session) and `user` aren't JSON-serializable values a human
    can type into the UI -- this node graph is designed to be inspected
    visually there, not executed standalone. Raising a clear error here
    beats a bare KeyError for that case.
    """
    try:
        return config["configurable"][key]
    except KeyError as exc:
        raise RuntimeError(
            f"Missing required config['configurable']['{key}'] -- this "
            "graph depends on the runtime dependencies ChatService "
            "provides (db session, user, stream_id) and isn't meant to be "
            "invoked standalone (e.g. from LangGraph Studio's manual run "
            "UI) without them."
        ) from exc


async def load_thread_state(state: AgentState, config: RunnableConfig) -> dict:
    """Placeholder for any per-turn thread-state hydration beyond what the
    checkpointer already restores automatically (prior `messages`). Kept as
    its own node to match the intended graph shape and as the natural place
    to add thread-level context later without reshaping the graph."""
    return {}


async def validate_knowledge_access(state: AgentState, config: RunnableConfig) -> dict:
    db = _config_get(config, "db")
    try:
        scope = await RetrievalService.resolve_scope(
            db,
            requesting_user_id=uuid.UUID(state["user_id"]),
            knowledge_type=state["knowledge_type"],
        )
    except Exception as exc:  # HTTPException from resolve_scope, or a bad type
        return {
            "access_denied": True,
            "denial_reason": getattr(exc, "detail", str(exc)),
        }
    return {
        "access_denied": False,
        "organization_id": str(scope.organization_id)
        if scope.organization_id
        else None,
    }


def _route_after_access_check(state: AgentState) -> str:
    return "access_denied" if state["access_denied"] else "condense_query"


async def access_denied_node(state: AgentState, config: RunnableConfig) -> dict:
    reason = state.get("denial_reason") or "Access denied"
    stream_manager.append_chunk(
        state["stream_id"], {"type": "error", "content": reason}
    )
    return {"response": reason, "citations": []}


async def condense_query(state: AgentState, config: RunnableConfig) -> dict:
    db = _config_get(config, "db")
    user = _config_get(config, "user")
    prior_messages = state.get("messages", [])
    if len(prior_messages) <= 1:
        return {"condensed_query": state["query"]}

    history_text = "\n".join(
        f"{getattr(m, 'type', 'user')}: {getattr(m, 'content', '')}"
        for m in prior_messages[:-1]
    )
    resolved = await LLMClientService.resolve_for_user(db, user)
    rewrite_prompt = (
        "Given this conversation history and a follow-up question, rewrite "
        "the follow-up as a standalone question. Reply with ONLY the "
        "rewritten question.\n\nHistory:\n"
        f"{history_text}\n\nFollow-up: {state['query']}"
    )
    condensed = ""
    async for delta in LLMClientService.stream_generate(
        resolved,
        system_prompt="You rewrite follow-up questions to be standalone.",
        messages=[{"role": "user", "content": rewrite_prompt}],
    ):
        condensed += delta
    return {"condensed_query": condensed.strip() or state["query"]}


async def hybrid_retrieve(state: AgentState, config: RunnableConfig) -> dict:
    organization_id = (
        uuid.UUID(state["organization_id"]) if state.get("organization_id") else None
    )
    scope = KnowledgeScope(
        knowledge_type=state["knowledge_type"],
        user_id=uuid.UUID(state["user_id"])
        if state["knowledge_type"] == "personal"
        else None,
        organization_id=organization_id,
    )
    chunks = await RetrievalService.retrieve(
        query=state["condensed_query"], scope=scope
    )
    return {
        "retrieved_chunks": [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "document_name": c.document_name,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "score": c.score,
            }
            for c in chunks
        ]
    }


async def generate(state: AgentState, config: RunnableConfig) -> dict:
    db = _config_get(config, "db")
    user = _config_get(config, "user")
    stream_id = state["stream_id"]

    context = (
        "\n\n".join(
            f"[{i + 1}] (from {chunk['document_name']}) {chunk['content']}"
            for i, chunk in enumerate(state["retrieved_chunks"])
        )
        or "(no relevant context was found)"
    )
    scope_label, scope_note = _KNOWLEDGE_SCOPE_PROMPTS[state["knowledge_type"]]
    system_prompt = _BASE_SYSTEM_PROMPT.format(
        knowledge_scope_label=scope_label,
        knowledge_scope_note=scope_note,
        context=context,
    )

    resolved = await LLMClientService.resolve_for_user(db, user)
    full_text = ""
    async for delta in LLMClientService.stream_generate(
        resolved,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": state["query"]}],
    ):
        full_text += delta
        stream_manager.append_chunk(stream_id, {"type": "token", "content": delta})

    return {"response": full_text}


async def build_citations(state: AgentState, config: RunnableConfig) -> dict:
    return {"citations": state["retrieved_chunks"]}


async def persist(state: AgentState, config: RunnableConfig) -> dict:
    db = _config_get(config, "db")
    assistant_message_id = uuid.UUID(_config_get(config, "assistant_message_id"))

    assistant_message = await db.get(ChatHistory, assistant_message_id)
    assistant_message.content = state["response"]
    assistant_message.chunks = state.get("citations", [])
    assistant_message.status = "failed" if state["access_denied"] else "completed"
    await db.commit()

    stream_manager.append_chunk(
        state["stream_id"],
        {
            "type": "done",
            "content": state["response"],
            "citations": state.get("citations", []),
        },
    )
    stream_manager.finish(state["stream_id"])
    return {}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("load_thread_state", load_thread_state)
    graph.add_node("validate_knowledge_access", validate_knowledge_access)
    graph.add_node("access_denied", access_denied_node)
    graph.add_node("condense_query", condense_query)
    graph.add_node("hybrid_retrieve", hybrid_retrieve)
    graph.add_node("generate", generate)
    graph.add_node("build_citations", build_citations)
    graph.add_node("persist", persist)

    graph.add_edge(START, "load_thread_state")
    graph.add_edge("load_thread_state", "validate_knowledge_access")
    graph.add_conditional_edges(
        "validate_knowledge_access",
        _route_after_access_check,
        {"access_denied": "access_denied", "condense_query": "condense_query"},
    )
    graph.add_edge("access_denied", "persist")
    graph.add_edge("condense_query", "hybrid_retrieve")
    graph.add_edge("hybrid_retrieve", "generate")
    graph.add_edge("generate", "build_citations")
    graph.add_edge("build_citations", "persist")
    graph.add_edge("persist", END)
    return graph


# A module-level, uncompiled StateGraph instance -- what `langgraph dev`
# (see langgraph.json) imports directly to render/inspect the workflow.
# It's the exact same topology ChatService compiles (with our own Postgres
# checkpointer) for real requests; the CLI compiles this one itself, with
# its own local dev-only persistence, purely for visualization/inspection
# (see `_config_get`'s docstring for why a manual Studio run can't fully
# execute a turn end-to-end).
graph = build_graph()
