"""The static LangGraph RAG workflow.

    START
      -> check_input_guardrail --(flagged)--> access_denied --> persist --> END
                                --(clean)--> load_thread_state
      -> validate_knowledge_access --(denied)--> access_denied --> persist --> END
                                    --(scope has zero documents)--> generate
                                    --(authorized)--> condense_query
      -> hybrid_retrieve (Pinecone hybrid dense+sparse search, then
                           Cohere-reranked down to the final chunk count)
      -> assess_retrieval_sufficiency
           --(insufficient, under cap)--> reformulate_query -> hybrid_retrieve (loop)
           --(sufficient, or cap reached)--> generate
      -> check_output_guardrail
      -> build_citations
      -> persist
      -> END

Both guardrails are on by default for every turn (architecture doc §10).

The retrieve/reformulate loop is hard-capped at MAX_RETRIEVAL_ATTEMPTS
total retrieval attempts (architecture doc §10) -- never unbounded.

Compiled once at import time (stateless aside from the checkpointer) and
reused across requests; per-request dependencies (the DB session, the
requesting user, the SSE stream_id) are threaded through via
`config["configurable"]`, LangGraph's documented mechanism for this,
rather than being rebuilt into the graph itself.
"""

import asyncio
import re
import uuid

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.database.models import ChatHistory
from app.graph.state import AgentState
from app.service.document_service import DocumentService
from app.service.guardrail_service import GuardrailService
from app.service.jev_service import JevService
from app.service.llm_client_service import LLMClientService
from app.service.reranker_service import RerankerService
from app.service.retrieval_service import KnowledgeScope, RetrievalService
from app.utils.stream_manager import stream_manager

# Hard cap on the retrieval-sufficiency retry loop: the initial attempt
# plus this many retries, after which generation proceeds with the best
# available context rather than looping forever (architecture doc §10).
MAX_RETRIEVAL_ATTEMPTS = 3

# A Noul-style probability at or above this counts as "sufficient" --
# applies identically whether the score came from the stub heuristic or
# (once configured) real JEV, since both return a 0.0-1.0 probability.
_SUFFICIENCY_THRESHOLD = 0.5

# hybrid_retrieve pulls this many hybrid (dense+sparse) candidates from
# Pinecone, then RerankerService narrows them down to _FINAL_CHUNK_COUNT
# -- reranking needs a wider pool to actually improve on Pinecone's own
# retrieval-time ordering (architecture doc §3: "the top ~20-50 hybrid
# results").
_RETRIEVAL_CANDIDATE_COUNT = 25
_FINAL_CHUNK_COUNT = 8

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
    "from, using exactly this format: plain ASCII square brackets with no "
    "space inside, e.g. [1] or [2][3]. Never use any other bracket style "
    "(no full-width/CJK brackets like 【1】, no parentheses, no "
    "superscripts) -- only [1], [2], etc. A sentence stating something "
    "from the context without a citation is a mistake.\n"
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

# Used instead of _BASE_SYSTEM_PROMPT when validate_knowledge_access finds
# the resolved scope has zero documents -- a different situation from "has
# documents but none matched this query" (which the normal prompt's rule 2
# already covers correctly). Without this distinction the model has no way
# to tell the two apart from an empty chunk list alone, and tends to phrase
# a reply as if it searched and came up empty, which is misleading when
# nothing was ever there to search.
_EMPTY_KNOWLEDGE_BASE_SYSTEM_PROMPT = (
    "You are Trench, a knowledge assistant that helps people find and "
    "understand information from their own documents -- both an "
    "individual's personal knowledge base and an organization's shared "
    "company knowledge base.\n\n"
    "You are currently answering from {knowledge_scope_label}, and it "
    "currently has ZERO documents in it -- this is not \"nothing matched "
    "this query\", it's that nothing has ever been uploaded there yet.\n\n"
    "Guidance:\n"
    "1. If the user's message is small talk or a greeting that doesn't "
    "need document content (e.g. \"hi\", \"how are you\"), just respond "
    "naturally and warmly -- don't volunteer the empty-knowledge-base "
    "situation unprompted.\n"
    "2. If answering would require looking something up in this knowledge "
    "base, say plainly and warmly that there's nothing in "
    "{knowledge_scope_label} yet, so there's nothing to search -- never "
    "phrase it as though you searched and simply found no match, since "
    "that implies documents exist when none do. {empty_scope_suggestion}\n"
    "3. Never invent or guess an answer from outside knowledge -- there is "
    "no context to work from right now.\n"
    "4. Be direct, brief, and friendly -- a sentence or two, not an "
    "apology paragraph."
)

_EMPTY_SCOPE_SUGGESTIONS = {
    "personal": "Suggest they upload a document to get started.",
    "company": "Suggest they ask a tenant admin or owner to upload one.",
}

# Used when the scope has documents (knowledge_base_empty is False) but
# this turn's retrieval -- even after the reformulate-and-retry loop --
# came back with zero chunks. Distinct from both other prompts: unlike
# _BASE_SYSTEM_PROMPT's "(no relevant context was found)" placeholder,
# this is explicit that there is NOTHING to cite, because the base prompt
# alone wasn't a strong enough instruction to reliably stop the model
# from answering out of its own general knowledge and inventing citation
# markers like "[1]" with no retrieved passage behind them -- citing a
# source that doesn't exist is worse than citing none at all.
_NO_RELEVANT_CONTEXT_SYSTEM_PROMPT = (
    "You are Trench, a knowledge assistant that helps people find and "
    "understand information from their own documents -- both an "
    "individual's personal knowledge base and an organization's shared "
    "company knowledge base.\n\n"
    "You are currently answering from {knowledge_scope_label}. A search "
    "was just run for this specific question, but nothing relevant was "
    "found among the documents there.\n\n"
    "Guidance:\n"
    "1. If the user's message is small talk or a greeting that doesn't "
    "need document content (e.g. \"hi\", \"how are you\"), just respond "
    "naturally and warmly.\n"
    "2. Otherwise, tell them plainly and briefly that nothing relevant to "
    "this question was found in {knowledge_scope_label} -- never answer "
    "from outside/general knowledge instead, even if you're confident "
    "you know the answer; that would be presenting unverified information "
    "as if it came from their documents.\n"
    "3. NEVER include citation markers like \"[1]\" or \"[2]\" anywhere in "
    "this reply -- there is no retrieved passage to cite, and a citation "
    "number with nothing behind it is actively misleading.\n"
    "4. Be direct and brief -- a sentence or two."
)


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


async def check_input_guardrail(state: AgentState, config: RunnableConfig) -> dict:
    """On by default for every turn (architecture doc §10) -- the very
    first node, so a flagged query never reaches authorization, retrieval,
    or generation at all. Routes to the same access_denied terminal node
    as an authorization failure, but with its own denial_reason text so
    the two are never confused with each other (architecture doc §5)."""
    flagged, reason = await GuardrailService.check_input(state["query"])
    return {
        "input_guardrail_flagged": flagged,
        # Reused by persist's status logic (failed vs completed) exactly
        # like an authorization denial -- both are "the turn stopped
        # early," just for different reasons (denial_reason carries which).
        "access_denied": flagged,
        "denial_reason": reason,
    }


def _route_after_input_guardrail(state: AgentState) -> str:
    return "access_denied" if state["input_guardrail_flagged"] else "load_thread_state"


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

    # Checked fresh every turn, from the scope just resolved above -- never
    # cached or inherited from a prior turn's checkpointed state, so
    # switching knowledge_type mid-thread (or a document finishing/failing
    # ingestion between turns) is always reflected correctly.
    if scope.knowledge_type == "personal":
        has_documents = await DocumentService.has_any_personal_documents(
            db, user_id=scope.user_id
        )
    else:
        has_documents = await DocumentService.has_any_company_documents(
            db, tenant_id=scope.tenant_id
        )

    return {
        "access_denied": False,
        "tenant_id": str(scope.tenant_id) if scope.tenant_id else None,
        "knowledge_base_empty": not has_documents,
    }


def _route_after_access_check(state: AgentState) -> str:
    if state["access_denied"]:
        return "access_denied"
    # Nothing to retrieve -- skip straight to generate rather than running
    # condense/hybrid_retrieve/the sufficiency-retry loop against a scope
    # that structurally has zero documents (and zero Pinecone/Cohere calls
    # wasted on it). generate() is told via knowledge_base_empty so it can
    # explain this honestly instead of treating it like a retrieval miss.
    if state["knowledge_base_empty"]:
        return "generate"
    return "condense_query"


async def access_denied_node(state: AgentState, config: RunnableConfig) -> dict:
    reason = state.get("denial_reason") or "Access denied"
    stream_manager.append_chunk(
        state["stream_id"], {"type": "error", "content": reason}
    )
    # Recorded into `messages` too (see `generate`'s docstring) so a
    # denied turn still shows up in conversational memory for later turns
    # -- otherwise a follow-up right after a denial would be condensed/
    # generated with a silent gap in what the assistant actually said.
    return {
        "response": reason,
        "citations": [],
        "messages": [{"role": "assistant", "content": reason}],
    }


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
    try:
        resolved = await LLMClientService.resolve_for_knowledge(
            db,
            user,
            knowledge_type=state["knowledge_type"],
            tenant_id=uuid.UUID(state["tenant_id"]) if state.get("tenant_id") else None,
        )
    except LLMClientService.MissingCredentialError:
        # Not fatal here -- condensing is an optimization, not the actual
        # generation step. Skip it and use the raw query; `generate` will
        # hit the same missing-credential condition and surface it to the
        # user properly.
        return {"condensed_query": state["query"]}
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
    tenant_id = uuid.UUID(state["tenant_id"]) if state.get("tenant_id") else None
    scope = KnowledgeScope(
        knowledge_type=state["knowledge_type"],
        user_id=uuid.UUID(state["user_id"])
        if state["knowledge_type"] == "personal"
        else None,
        tenant_id=tenant_id,
    )
    chunks = await RetrievalService.retrieve(
        query=state["condensed_query"],
        scope=scope,
        top_k=_RETRIEVAL_CANDIDATE_COUNT,
    )
    candidates = [
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
    reranked = await RerankerService.rerank(
        state["condensed_query"], candidates, top_k=_FINAL_CHUNK_COUNT
    )
    return {"retrieved_chunks": reranked}


def _sufficiency_stub_heuristic(retrieved_chunks: list[dict]) -> float:
    """Stub fallback for JevService.ask_noul while TypeSafe/JEV isn't
    configured -- the top retrieved chunk's own hybrid-search score,
    treated as a stand-in probability. Documented placeholder, not a
    tuned relevance model; replaced by the real JEV call once available."""
    if not retrieved_chunks:
        return 0.0
    return max(chunk["score"] for chunk in retrieved_chunks)


async def assess_retrieval_sufficiency(
    state: AgentState, config: RunnableConfig
) -> dict:
    """Asks whether the just-retrieved context actually answers the
    condensed query -- the self-assessment point that decides whether the
    retrieval-reformulate loop should run again (see _route_after_
    sufficiency_check) or proceed to generation."""
    score = await JevService.ask_noul(
        "Does this retrieved context contain enough information to "
        "answer the query?",
        stub_fallback=lambda: _sufficiency_stub_heuristic(state["retrieved_chunks"]),
    )
    return {
        "retrieval_sufficient": score >= _SUFFICIENCY_THRESHOLD,
        "retrieval_attempts": state.get("retrieval_attempts", 0) + 1,
    }


def _route_after_sufficiency_check(state: AgentState) -> str:
    attempts_exhausted = state["retrieval_attempts"] >= MAX_RETRIEVAL_ATTEMPTS
    if state["retrieval_sufficient"] or attempts_exhausted:
        return "generate"
    return "reformulate_query"


async def reformulate_query(state: AgentState, config: RunnableConfig) -> dict:
    """Rewrites the query with different phrasing/broader terms after an
    insufficient retrieval attempt, then loops back to hybrid_retrieve.
    Only reached under MAX_RETRIEVAL_ATTEMPTS (see _route_after_
    sufficiency_check), so this never runs an unbounded number of times.
    """
    db = _config_get(config, "db")
    user = _config_get(config, "user")
    try:
        resolved = await LLMClientService.resolve_for_knowledge(
            db,
            user,
            knowledge_type=state["knowledge_type"],
            tenant_id=uuid.UUID(state["tenant_id"])
            if state.get("tenant_id")
            else None,
        )
    except LLMClientService.MissingCredentialError:
        # Not fatal -- reformulation is an optimization; retry with the
        # same query rather than erroring (generate still enforces the
        # credential requirement properly once the loop ends).
        return {}
    reformulate_prompt = (
        "The following search query did not retrieve enough relevant "
        "information to answer the question. Rewrite it with different "
        "phrasing or broader terms that might match more relevant "
        "content. Reply with ONLY the rewritten query.\n\n"
        f"Original query: {state['condensed_query']}"
    )
    reformulated = ""
    async for delta in LLMClientService.stream_generate(
        resolved,
        system_prompt="You rewrite search queries to improve retrieval.",
        messages=[{"role": "user", "content": reformulate_prompt}],
    ):
        reformulated += delta
    return {"condensed_query": reformulated.strip() or state["condensed_query"]}


def _to_provider_message(message: object) -> dict[str, str]:
    """Converts a LangChain BaseMessage (as restored from the checkpointer)
    into the plain {"role", "content"} shape LLMClientService.stream_generate
    expects, provider-agnostically."""
    role = "assistant" if getattr(message, "type", "human") == "ai" else "user"
    return {"role": role, "content": getattr(message, "content", "")}


async def generate(state: AgentState, config: RunnableConfig) -> dict:
    """Generates the assistant's reply from the retrieved context *and* the
    full prior conversation (every earlier turn in `state["messages"]"),
    not just this turn's raw query in isolation -- this is what makes the
    agent genuinely conversational: a short follow-up like "continue" or
    "what about the second point" is meaningless without the turns before
    it, and previously only the bare current-turn query was ever sent to
    the model, discarding history entirely regardless of interruption.

    The assistant's own reply is written back into `messages` (returned
    here, merged by the `add_messages` reducer) so the *next* turn's
    history includes what was actually said, not just what was asked --
    without this, conversational memory would stay permanently one-sided
    (user turns only) no matter how many turns accumulate.
    """
    db = _config_get(config, "db")
    user = _config_get(config, "user")
    stream_id = state["stream_id"]

    scope_label, scope_note = _KNOWLEDGE_SCOPE_PROMPTS[state["knowledge_type"]]
    if state["knowledge_base_empty"]:
        system_prompt = _EMPTY_KNOWLEDGE_BASE_SYSTEM_PROMPT.format(
            knowledge_scope_label=scope_label,
            empty_scope_suggestion=_EMPTY_SCOPE_SUGGESTIONS[state["knowledge_type"]],
        )
    elif not state["retrieved_chunks"]:
        system_prompt = _NO_RELEVANT_CONTEXT_SYSTEM_PROMPT.format(
            knowledge_scope_label=scope_label
        )
    else:
        context = "\n\n".join(
            f"[{i + 1}] (from {chunk['document_name']}) {chunk['content']}"
            for i, chunk in enumerate(state["retrieved_chunks"])
        )
        system_prompt = _BASE_SYSTEM_PROMPT.format(
            knowledge_scope_label=scope_label,
            knowledge_scope_note=scope_note,
            context=context,
        )

    # Every message so far is `state["messages"]` up to (but not
    # including) the current turn's own raw query, which `initial_state`
    # always appends last -- see ChatService._run_generation.
    history = [_to_provider_message(m) for m in state["messages"][:-1]]
    conversation = [*history, {"role": "user", "content": state["query"]}]

    try:
        resolved = await LLMClientService.resolve_for_knowledge(
            db,
            user,
            knowledge_type=state["knowledge_type"],
            tenant_id=uuid.UUID(state["tenant_id"]) if state.get("tenant_id") else None,
        )
    except LLMClientService.MissingCredentialError as exc:
        reason = str(exc)
        stream_manager.append_chunk(stream_id, {"type": "error", "content": reason})
        return {
            "response": reason,
            "citations": [],
            "messages": [{"role": "assistant", "content": reason}],
        }
    full_text = ""
    async for delta in LLMClientService.stream_generate(
        resolved,
        system_prompt=system_prompt,
        messages=conversation,
    ):
        full_text += delta
        stream_manager.append_chunk(stream_id, {"type": "token", "content": delta})

    return {
        "response": full_text,
        "messages": [{"role": "assistant", "content": full_text}],
    }


async def check_output_guardrail(state: AgentState, config: RunnableConfig) -> dict:
    """On by default for every turn (architecture doc §10) -- redacts any
    credential-shaped substring from the generated response before it's
    persisted or included in the stream's final "done" event.

    Known limitation, not silently hidden: `generate` already streams
    each token live as it's produced (see its own `stream_manager.
    append_chunk(..., {"type": "token", ...})` calls), so this guardrail
    cannot retroactively un-stream tokens the client already received --
    it protects the persisted ChatHistory record and the final "done"
    event's content, which is real, worthwhile defense-in-depth (the
    architecture doc's own framing), just not a guarantee against a leak
    having been visible transiently during streaming. Closing that fully
    would mean buffering the entire response before streaming any of it,
    which trades away streaming itself -- flagged here rather than
    silently implemented as a stronger guarantee than it actually is.
    """
    sanitized, flags = GuardrailService.check_output(state["response"])
    return {"response": sanitized, "guardrail_flags": flags}


_CITATION_RELEVANCE_THRESHOLD = 0.5

# A small, documented, non-exhaustive stopword list for the citation-
# relevance stub heuristic -- not a general-purpose NLP tool, just enough
# to keep the word-overlap signal from being dominated by filler words.
_STOPWORDS = frozenset(
    "a an the is are was were be been being of for to in on at by with "
    "and or but not this that these those it its as".split()
)


def _significant_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _citation_relevance_stub_heuristic(chunk_content: str, response: str) -> float:
    """Stub fallback for JevService.ask_noul while TypeSafe/JEV isn't
    configured -- what fraction of the chunk's significant words also
    appear in the generated response. Documented placeholder, not a
    tuned relevance model."""
    chunk_words = _significant_words(chunk_content)
    if not chunk_words:
        return 0.0
    response_words = _significant_words(response)
    overlap = chunk_words & response_words
    return len(overlap) / len(chunk_words)


async def build_citations(state: AgentState, config: RunnableConfig) -> dict:
    """Keeps only the retrieved chunks actually reflected in the generated
    response -- replaces blindly echoing back every retrieved chunk
    regardless of whether the model used it. Each chunk's relevance is
    asked independently and in parallel (asyncio.gather), matching JEV's
    own atomic-question design (architecture doc §4).

    Each surviving chunk keeps a `citation_number` equal to its 1-based
    position in `state["retrieved_chunks"]` -- the exact numbering
    `generate()` put in the prompt and told the model to cite with
    ("[1]", "[2]", ...). Without this, a filtered-out chunk shifts every
    later survivor's array position, so "the 2nd surviving chunk" would
    stop meaning the same thing as the model's own "[2]" the moment
    anything earlier gets dropped -- the frontend's citation markers must
    match by this number, never by position in the returned list.

    Without real JEV configured, this judgment falls back to
    _citation_relevance_stub_heuristic -- plain word-overlap between the
    chunk and the response. That signal breaks down specifically for any
    answer that *computes* something from the source rather than quoting
    it (e.g. "512 x 8 = 4,096" when the chunk only states the formula and
    rank, never the product) -- confirmed scoring ~0.26 against a
    genuinely-correct source chunk, well under the threshold, for exactly
    this kind of question. Filtering on that signal would silently drop
    real citations for a whole common class of questions, which is worse
    than not filtering at all. So in stub mode every retrieved chunk is
    kept, unfiltered, relying on hybrid retrieval + Cohere rerank (both
    already run before this node) to have done the actual relevance
    work -- real per-chunk "was this actually used" filtering only
    applies once a real JEV judge is configured and can be trusted with
    that narrower, harder question.
    """
    chunks = state["retrieved_chunks"]
    if not chunks:
        return {"citations": []}

    if not JevService.is_configured():
        return {
            "citations": [
                {**chunk, "citation_number": i + 1} for i, chunk in enumerate(chunks)
            ]
        }

    scores = await asyncio.gather(
        *(
            JevService.ask_noul(
                "Was this chunk's content actually used or referenced in "
                "the final answer?",
                stub_fallback=lambda c=chunk: _citation_relevance_stub_heuristic(
                    c["content"], state["response"]
                ),
            )
            for chunk in chunks
        )
    )
    return {
        "citations": [
            {**chunk, "citation_number": i + 1}
            for i, (chunk, score) in enumerate(zip(chunks, scores, strict=True))
            if score >= _CITATION_RELEVANCE_THRESHOLD
        ]
    }


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
    graph.add_node("check_input_guardrail", check_input_guardrail)
    graph.add_node("load_thread_state", load_thread_state)
    graph.add_node("validate_knowledge_access", validate_knowledge_access)
    graph.add_node("access_denied", access_denied_node)
    graph.add_node("condense_query", condense_query)
    graph.add_node("hybrid_retrieve", hybrid_retrieve)
    graph.add_node("assess_retrieval_sufficiency", assess_retrieval_sufficiency)
    graph.add_node("reformulate_query", reformulate_query)
    graph.add_node("generate", generate)
    graph.add_node("check_output_guardrail", check_output_guardrail)
    graph.add_node("build_citations", build_citations)
    graph.add_node("persist", persist)

    graph.add_edge(START, "check_input_guardrail")
    graph.add_conditional_edges(
        "check_input_guardrail",
        _route_after_input_guardrail,
        {"access_denied": "access_denied", "load_thread_state": "load_thread_state"},
    )
    graph.add_edge("load_thread_state", "validate_knowledge_access")
    graph.add_conditional_edges(
        "validate_knowledge_access",
        _route_after_access_check,
        {
            "access_denied": "access_denied",
            "condense_query": "condense_query",
            "generate": "generate",
        },
    )
    graph.add_edge("access_denied", "persist")
    graph.add_edge("condense_query", "hybrid_retrieve")
    graph.add_edge("hybrid_retrieve", "assess_retrieval_sufficiency")
    graph.add_conditional_edges(
        "assess_retrieval_sufficiency",
        _route_after_sufficiency_check,
        {"generate": "generate", "reformulate_query": "reformulate_query"},
    )
    graph.add_edge("reformulate_query", "hybrid_retrieve")
    graph.add_edge("generate", "check_output_guardrail")
    graph.add_edge("check_output_guardrail", "build_citations")
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
