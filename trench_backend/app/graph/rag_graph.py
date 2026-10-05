"""The static LangGraph RAG workflow.

    START
      -> load_thread_state
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

There is no input guardrail -- removed; see GuardrailService's own
docstring for why what remains (check_output) is a different category of
check. access_denied is still reachable, from validate_knowledge_access's
own authorization failures.

check_output_guardrail is on by default for every turn.

The retrieve/reformulate loop is hard-capped at MAX_RETRIEVAL_ATTEMPTS
total retrieval attempts -- never unbounded.

Compiled once at import time (stateless aside from the checkpointer) and
reused across requests; per-request dependencies (the DB session, the
requesting user, the SSE stream_id) are threaded through via
`config["configurable"]`, LangGraph's documented mechanism for this,
rather than being rebuilt into the graph itself.

No node here wraps its own Pinecone or Cohere call in a try/except --
deliberately. ChatService._run_generation already wraps the entire
ainvoke() of this graph in one outer try/except: any uncaught exception
from any node (a Pinecone timeout in hybrid_retrieve, a Cohere error in
RerankerService.rerank, anything else) is caught there, persisted as the
ChatHistory row's status="failed" with the exception's message as
content, and streamed to the client as a {"type": "error"} event. Adding
a second, narrower try/except around an individual call here would only
duplicate that handling with a different message, not add any real
protection.
"""

import logging
import re
import uuid

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.database.models import ChatHistory
from app.graph.state import AgentState
from app.service.document_service import DocumentService
from app.service.guardrail_service import GuardrailService
from app.service.llm_client_service import LLMClientService
from app.service.reranker_service import RerankerService
from app.service.retrieval_service import KnowledgeScope, RetrievalService
from app.utils.stream_manager import stream_manager

logger = logging.getLogger(__name__)

# Hard cap on the retrieval-sufficiency retry loop: the initial attempt
# plus this many retries, after which generation proceeds with the best
# attempt seen so far (see assess_retrieval_sufficiency's best_retrieved_
# chunks tracking) rather than looping forever.
MAX_RETRIEVAL_ATTEMPTS = 2

# If the latest attempt's top rerank score is below even this (well under
# RETRIEVAL_SUFFICIENCY_MIN_RERANK_SCORE), the namespace almost certainly
# has nothing relevant at all -- reformulating the same unanswerable
# query is very unlikely to help, so _route_after_sufficiency_check skips
# straight to generate() instead of spending a retry on it. Starting
# point only, meant to be tuned against real traffic.
RETRIEVAL_SKIP_RETRY_BELOW = 0.05

# How many of the most recent prior messages (user+assistant turns,
# excluding the current query) are sent as conversation history to both
# condense_query and generate() -- an unbounded history would eventually
# make every turn resend the whole conversation, growing token cost and
# latency without bound as a thread gets long.
MAX_HISTORY_MESSAGES = 10

# The top chunk's Cohere rerank relevance score (see RerankerService.rerank
# -- "score" on a retrieved chunk is that relevance score, not Pinecone's
# hybrid-search score) must be at least this high to count as "sufficient"
# context to answer from. Not derived from any formal calibration --
# starting point only, meant to be tuned against real traffic.
RETRIEVAL_SUFFICIENCY_MIN_RERANK_SCORE = 0.3

# Pinecone (and then Cohere's rerank) almost always return *something*
# from a non-empty namespace, even when nothing retrieved is genuinely
# relevant to the query -- a non-empty best_retrieved_chunks is therefore
# not by itself evidence the question is answerable from it. A chunk must
# clear this (much lower than the sufficiency bar above) rerank score to
# count as usable context at all; below it, generate() treats the turn as
# having no relevant context, same as an empty list. Starting point only,
# meant to be tuned against real traffic.
MIN_USABLE_RERANK_SCORE = 0.1

# hybrid_retrieve pulls this many hybrid (dense+sparse) candidates from
# Pinecone, then RerankerService narrows them down to _FINAL_CHUNK_COUNT
# -- reranking needs a wider pool to actually improve on Pinecone's own
# retrieval-time ordering.
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
    """Sets both condensed_query and original_condensed_query to the same
    value -- the raw query standalone-rewritten against recent history, or
    the raw query itself if there's no history or rewriting fails.
    original_condensed_query is never touched again after this node; it's
    what reformulate_query rewrites from on every retry (see its own
    docstring), while condensed_query is what each retry attempt mutates.
    """
    db = _config_get(config, "db")
    prior_messages = state.get("messages", [])
    if len(prior_messages) <= 1:
        return {
            "condensed_query": state["query"],
            "original_condensed_query": state["query"],
        }

    history_text = "\n".join(
        f"{getattr(m, 'type', 'user')}: {getattr(m, 'content', '')}"
        for m in prior_messages[:-1][-MAX_HISTORY_MESSAGES:]
    )
    try:
        # The platform default Groq model/key, never the user's selected
        # model or BYOK credential -- condensing is a cheap, internal
        # rewrite step, not worth spending a BYOK provider's quota/cost
        # on, and must not fail the turn just because a credential is
        # missing (see resolve_platform_default's own docstring).
        resolved = await LLMClientService.resolve_platform_default(db)
    except Exception:
        # Not fatal here -- condensing is an optimization, not the actual
        # generation step. Skip it and use the raw query; `generate` still
        # resolves the user's real model/credential separately and will
        # surface any real problem there.
        return {
            "condensed_query": state["query"],
            "original_condensed_query": state["query"],
        }
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
    final_query = condensed.strip() or state["query"]
    return {
        "condensed_query": final_query,
        "original_condensed_query": final_query,
    }


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


def _top_rerank_score(chunks: list[dict]) -> float:
    if not chunks:
        return 0.0
    return max(chunk["score"] for chunk in chunks)


async def assess_retrieval_sufficiency(
    state: AgentState, config: RunnableConfig
) -> dict:
    """Decides whether this attempt's retrieved context is good enough to
    answer from -- the self-assessment point that decides whether the
    retrieval-reformulate loop should run again (see _route_after_
    sufficiency_check) or proceed to generation. "Good enough" is the top
    chunk's Cohere rerank score clearing RETRIEVAL_SUFFICIENCY_MIN_RERANK_
    SCORE; an empty chunk list is never sufficient.

    Also tracks best_retrieved_chunks across the retry loop: a later
    reformulated attempt is not guaranteed to score higher than an
    earlier one, so whichever attempt has the highest top score so far is
    kept here for generate()/build_citations() to use, rather than
    whatever the *last* attempt happened to retrieve.
    """
    chunks = state["retrieved_chunks"]
    top_score = _top_rerank_score(chunks)
    attempt_number = state.get("retrieval_attempts", 0) + 1
    sufficient = top_score >= RETRIEVAL_SUFFICIENCY_MIN_RERANK_SCORE

    best_chunks = state.get("best_retrieved_chunks") or []
    if not state.get("retrieval_attempts") or top_score > _top_rerank_score(
        best_chunks
    ):
        best_chunks = chunks

    logger.info(
        "Retrieval sufficiency check | attempt=%d top_score=%.3f sufficient=%s",
        attempt_number,
        top_score,
        sufficient,
    )

    return {
        "retrieval_sufficient": sufficient,
        "retrieval_attempts": attempt_number,
        "best_retrieved_chunks": best_chunks,
        "last_retrieval_top_score": top_score,
    }


def _route_after_sufficiency_check(state: AgentState) -> str:
    if state["retrieval_sufficient"]:
        return "generate"
    if state["retrieval_attempts"] >= MAX_RETRIEVAL_ATTEMPTS:
        return "generate"
    if state["last_retrieval_top_score"] < RETRIEVAL_SKIP_RETRY_BELOW:
        # Below even the much-lower "is there anything at all" floor --
        # the namespace almost certainly has nothing relevant, so
        # reformulating the same unanswerable query and retrying is very
        # unlikely to help. Spend generate()'s honest "nothing relevant
        # was found" response instead of a retry that would just burn a
        # reformulate_query LLM call for the same empty result.
        return "generate"
    return "reformulate_query"


async def reformulate_query(state: AgentState, config: RunnableConfig) -> dict:
    """Rewrites original_condensed_query (never condensed_query itself --
    see AgentState's own docstring on the two fields) with different
    phrasing/broader terms after an insufficient retrieval attempt, then
    loops back to hybrid_retrieve. Always rewriting from the same
    original standalone question, rather than from whatever the previous
    reformulation produced, keeps a second retry from compounding drift
    onto an already-once-reworded query. Only reached under
    MAX_RETRIEVAL_ATTEMPTS (see _route_after_sufficiency_check), so this
    never runs an unbounded number of times.
    """
    db = _config_get(config, "db")
    try:
        # The platform default Groq model/key -- see condense_query's own
        # comment on why these internal rewrite steps never use the
        # user's selected model/BYOK credential.
        resolved = await LLMClientService.resolve_platform_default(db)
    except Exception:
        # Not fatal -- reformulation is an optimization; retry with the
        # same query rather than erroring (generate still resolves the
        # user's real model/credential separately once the loop ends).
        return {}
    reformulate_prompt = (
        "The following search query did not retrieve enough relevant "
        "information to answer the question. Rewrite it with different "
        "phrasing or broader terms that might match more relevant "
        "content. Reply with ONLY the rewritten query.\n\n"
        f"Original query: {state['original_condensed_query']}"
    )
    reformulated = ""
    async for delta in LLMClientService.stream_generate(
        resolved,
        system_prompt="You rewrite search queries to improve retrieval.",
        messages=[{"role": "user", "content": reformulate_prompt}],
    ):
        reformulated += delta
    return {
        "condensed_query": reformulated.strip() or state["original_condensed_query"]
    }


def _context_chunks(state: AgentState) -> list[dict]:
    """The subset of best_retrieved_chunks actually usable as generation
    context, in their original order -- below MIN_USABLE_RERANK_SCORE, a
    chunk is noise Pinecone/Cohere returned because *something* has to
    come back from a non-empty namespace, not a genuinely relevant match.

    Used by BOTH generate() (prompt numbering and the empty/no-context
    decision) and build_citations() (validating "[n]" markers against
    this same list's length) so a chunk's position here is the single
    source of truth for its citation_number in both places -- they can
    never drift apart into two different ideas of "chunk 2".
    """
    return [
        chunk
        for chunk in state["best_retrieved_chunks"]
        if chunk["score"] >= MIN_USABLE_RERANK_SCORE
    ]


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
    context_chunks = _context_chunks(state)
    if state["knowledge_base_empty"]:
        prompt_case = "empty_kb"
        system_prompt = _EMPTY_KNOWLEDGE_BASE_SYSTEM_PROMPT.format(
            knowledge_scope_label=scope_label,
            empty_scope_suggestion=_EMPTY_SCOPE_SUGGESTIONS[state["knowledge_type"]],
        )
    elif not context_chunks:
        prompt_case = "no_context"
        system_prompt = _NO_RELEVANT_CONTEXT_SYSTEM_PROMPT.format(
            knowledge_scope_label=scope_label
        )
    else:
        prompt_case = "normal"
        context = "\n\n".join(
            f"[{i + 1}] (from {chunk['document_name']}) {chunk['content']}"
            for i, chunk in enumerate(context_chunks)
        )
        system_prompt = _BASE_SYSTEM_PROMPT.format(
            knowledge_scope_label=scope_label,
            knowledge_scope_note=scope_note,
            context=context,
        )
    logger.info(
        "Generate | prompt_case=%s context_chunks=%d",
        prompt_case,
        len(context_chunks),
    )

    # Every message so far is `state["messages"]` up to (but not
    # including) the current turn's own raw query, which `initial_state`
    # always appends last -- see ChatService._run_generation. Windowed to
    # the last MAX_HISTORY_MESSAGES so an unbounded thread doesn't keep
    # growing this turn's token cost/latency without bound; the full
    # history is still what's persisted (see ChatHistory/Thread), only
    # what's sent to the model here is capped.
    history = [
        _to_provider_message(m) for m in state["messages"][:-1][-MAX_HISTORY_MESSAGES:]
    ]
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
    """On by default for every turn -- redacts any
    credential-shaped substring from the generated response before it's
    persisted or included in the stream's final "done" event.

    Known limitation, not silently hidden: `generate` already streams
    each token live as it's produced (see its own `stream_manager.
    append_chunk(..., {"type": "token", ...})` calls), so this guardrail
    cannot retroactively un-stream tokens the client already received --
    it protects the persisted ChatHistory record and the final "done"
    event's content, which is real, worthwhile defense-in-depth, just not
    a guarantee against a leak having been visible transiently during
    streaming. Closing that fully
    would mean buffering the entire response before streaming any of it,
    which trades away streaming itself -- flagged here rather than
    silently implemented as a stronger guarantee than it actually is.
    """
    sanitized, flags = GuardrailService.check_output(state["response"])
    return {"response": sanitized, "guardrail_flags": flags}


_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")


async def build_citations(state: AgentState, config: RunnableConfig) -> dict:
    """Keeps only the retrieved chunks the model actually cited in its
    response, by reading the "[n]" markers back out of the generated text
    -- replaces blindly echoing back every retrieved chunk regardless of
    whether the model used it.

    Each surviving chunk keeps a `citation_number` equal to its 1-based
    position in `_context_chunks(state)` -- the exact same filtered,
    ordered list generate() builds its numbered prompt from (see
    _context_chunks's own docstring for why both read the same helper
    rather than each filtering independently), and the same number the
    regex here pulls back out of the response. A marker outside that
    range (the model hallucinated a citation number nothing was ever put
    at) is dropped rather than raising or guessing which chunk it meant;
    a duplicate marker keeps only one citation. The frontend's citation
    markers must match by this number, never by position in the returned
    list.
    """
    chunks = _context_chunks(state)
    if not chunks:
        return {"citations": []}

    cited_numbers = {
        int(match) for match in _CITATION_MARKER_RE.findall(state["response"])
    }
    valid_numbers = sorted(n for n in cited_numbers if 1 <= n <= len(chunks))

    return {
        "citations": [
            {**chunks[n - 1], "citation_number": n} for n in valid_numbers
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

    graph.add_edge(START, "load_thread_state")
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
