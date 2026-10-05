# Agentic RAG Pipeline

## Overview

The pipeline implements a knowledge assistant that retrieves from and generates responses based on user-uploaded documents. "Agentic" here means self-assessment (retrieval sufficiency checks), a bounded retry/reformulation loop (up to `MAX_RETRIEVAL_ATTEMPTS = 2` total attempts when retrieval scores are low), and scope-aware routing (personal vs company knowledge base authorization). The graph is a LangGraph StateGraph compiled once at import time with a Postgres checkpointer for thread state persistence across requests.

There is no input guardrail. One used to exist here (a local pattern match for prompt-injection/instruction-override phrasings); it was removed because real protection against a query that would misuse retrieval or generation already comes from `validate_knowledge_access`'s scope-checked authorization and from the system prompt's own rules, and the input guardrail added a second, weaker, pattern-matched layer of the same thing without gating anything those don't already gate. See `trench_backend/app/service/guardrail_service.py`'s module docstring. The output guardrail (`check_output`) remains and runs on every turn -- it's a different category of check (redacting credential-shaped substrings from the generated response), not a replacement for authorization.

## Graph Diagram

```mermaid
flowchart TD
    START([START]) --> load_thread_state

    load_thread_state --> validate_knowledge_access
    validate_knowledge_access -->|denied| access_denied
    validate_knowledge_access -->|zero documents| generate
    validate_knowledge_access -->|authorized| condense_query

    access_denied --> persist
    condense_query --> hybrid_retrieve
    hybrid_retrieve --> assess_retrieval_sufficiency

    assess_retrieval_sufficiency -->|insufficient, under cap, score not hopeless| reformulate_query
    assess_retrieval_sufficiency -->|sufficient, cap reached, or score hopeless| generate

    reformulate_query --> hybrid_retrieve
    generate --> check_output_guardrail
    check_output_guardrail --> build_citations
    build_citations --> persist
    persist --> END([END])
```

## One Chat Turn, Node by Node

| Node | Reads from state | Writes to state | External calls | Failure behavior |
|------|------------------|------------------|-----------------|------------------|
| `load_thread_state` | None | None (empty dict) | None | Never fails |
| `validate_knowledge_access` | `user_id`, `knowledge_type` | `tenant_id`, `access_denied`, `denial_reason`, `knowledge_base_empty` | DB: `RetrievalService.resolve_scope`, `DocumentService.has_any_personal_documents`/`has_any_company_documents` | On error: sets `access_denied=True`, `denial_reason=exc.detail` |
| `_route_after_access_check` | `access_denied`, `knowledge_base_empty` | None (routing only) | None | Returns route string |
| `access_denied` | `denial_reason`, `stream_id` | `response`, `citations`, `messages` | None (writes to stream_manager) | Never fails |
| `condense_query` | `messages`, `query` | `condensed_query`, `original_condensed_query` | LLM: `LLMClientService.resolve_platform_default` + `stream_generate` (if history exists) | On resolution/generation failure: both fields fall back to the raw query unchanged |
| `hybrid_retrieve` | `condensed_query`, `tenant_id`, `knowledge_type`, `user_id` | `retrieved_chunks` | Pinecone: `RetrievalService.retrieve` (`_RETRIEVAL_CANDIDATE_COUNT` candidates), Cohere: `RerankerService.rerank` (down to `_FINAL_CHUNK_COUNT`) | No try/except here -- see "Failure Modes" below |
| `assess_retrieval_sufficiency` | `retrieved_chunks`, `retrieval_attempts`, `best_retrieved_chunks` | `retrieval_sufficient`, `retrieval_attempts`, `best_retrieved_chunks`, `last_retrieval_top_score` | None | Never fails; logs attempt number, top score, sufficiency |
| `_route_after_sufficiency_check` | `retrieval_sufficient`, `retrieval_attempts`, `last_retrieval_top_score` | None (routing only) | None | Returns route string |
| `reformulate_query` | `original_condensed_query` | `condensed_query` | LLM: `LLMClientService.resolve_platform_default` + `stream_generate` | On resolution/generation failure: returns `{}` (no rewrite, retry proceeds with the unreformulated query) |
| `generate` | `best_retrieved_chunks`, `messages`, `query`, `knowledge_base_empty`, `knowledge_type`, `tenant_id`, `stream_id` | `response`, `messages` | LLM: `LLMClientService.resolve_for_knowledge` + `stream_generate` | On `MissingCredentialError`: writes error to stream, returns error as response |
| `check_output_guardrail` | `response` | `response` (sanitized), `guardrail_flags` | None (local pattern match) | Never fails |
| `build_citations` | `response`, `best_retrieved_chunks` | `citations` | None | Never fails |
| `persist` | `response`, `citations`, `access_denied`, `stream_id` | None (updates DB, no state writes) | DB: update `ChatHistory` row | Never fails |

## AgentState Reference

| Field | Type | Set by node | Purpose |
|-------|------|-------------|---------|
| `user_id` | str | Initial state (ChatService) | UUID of the requesting user |
| `thread_id` | str | Initial state (ChatService) | Thread identifier for checkpointer |
| `stream_id` | str | Initial state (ChatService) | SSE stream identifier (assistant message UUID) |
| `query` | str | Initial state (ChatService) | Raw user query text |
| `knowledge_type` | str | Initial state (ChatService) | "personal" or "company" |
| `guardrail_flags` | list[str] | `check_output_guardrail` | List of redaction reasons from the output guardrail |
| `tenant_id` | str \| None | `validate_knowledge_access` | Resolved tenant UUID for company queries |
| `access_denied` | bool | `validate_knowledge_access` | Whether access was denied (authorization only -- no input guardrail) |
| `denial_reason` | str \| None | `validate_knowledge_access` | Human-readable denial reason |
| `knowledge_base_empty` | bool | `validate_knowledge_access` | Whether the resolved scope has zero documents |
| `condensed_query` | str | `condense_query`, `reformulate_query` | Query used for retrieval this attempt (rewritten or original); mutated on each retry |
| `original_condensed_query` | str | `condense_query` | Set once and never touched again -- what `condensed_query` started this turn as, before any `reformulate_query` rewrite. `reformulate_query` always rewrites from this, never from `condensed_query` itself, so a second retry rewrites the original standalone question again rather than compounding drift onto an already-once-reformulated query |
| `retrieved_chunks` | list[dict] | `hybrid_retrieve` | Chunks from the latest retrieval attempt |
| `best_retrieved_chunks` | list[dict] | `assess_retrieval_sufficiency` | Highest-scoring chunks across all retrieval attempts |
| `retrieval_sufficient` | bool | `assess_retrieval_sufficiency` | Whether top chunk score meets sufficiency threshold |
| `retrieval_attempts` | int | `assess_retrieval_sufficiency` | Number of retrieval attempts (1-indexed) |
| `last_retrieval_top_score` | float | `assess_retrieval_sufficiency` | The latest (not best-so-far) attempt's top rerank score -- read by `_route_after_sufficiency_check` to skip straight to `generate` when even the latest attempt scored below `RETRIEVAL_SKIP_RETRY_BELOW` |
| `response` | str | `generate`, `check_output_guardrail`, `access_denied` | Final assistant response text |
| `citations` | list[dict] | `build_citations` | Chunks actually cited in the response |
| `messages` | Annotated[list, add_messages] | `generate`, `access_denied` | Conversation history (LangChain BaseMessage objects) |

## Ingestion Path

### Parsing
`ParsingService.parse` in `trench_backend/app/service/parsing_service.py`:
- PDF/DOCX: LlamaParse with `result_type="markdown"` (structure-aware extraction)
- TXT/MD: Direct UTF-8 decode (no LlamaParse call)

### Chunking
`ChunkingService.chunk` in `trench_backend/app/service/chunking_service.py` returns a list of `Chunk(text, heading_path)`, built in two passes:
1. `MarkdownHeaderTextSplitter` (`strip_headers=False`) splits the parsed text on Markdown headers (`#` through `####`) first, so a chunk never straddles two unrelated sections. Each resulting section carries the header text found above it (e.g. `h1="Refunds"`, `h2="Timelines"`).
2. Each section is then split with `MarkdownTextSplitter` (`CHUNK_SIZE = 512`, `CHUNK_OVERLAP = 80`, both measured via `count_tokens` / `tiktoken.get_encoding("cl100k_base")` as the length function).
- `heading_path` is the section's header values joined with `" > "` (e.g. `"Refunds > Timelines"`); empty for TXT uploads or any section with no headers above it.
- Filters out empty chunks and assigns sequential indices per document (by list order).

### Embedding
`EmbeddingService.embed_texts`/`embed_query` in `trench_backend/app/service/embedding_service.py`:
- Model: `gemini-embedding-001`
- Dimensionality: `DIMENSIONS = 768` (via `output_dimensionality=768`)
- Task type for documents: `RETRIEVAL_DOCUMENT`; for queries: `RETRIEVAL_QUERY`
- **L2 normalization**: per Google's docs (ai.google.dev/gemini-api/docs/embeddings), `gemini-embedding-001` only returns unit-length vectors at its native 3072 dimensions -- at the truncated `output_dimensionality=768` used here, the API returns raw, unnormalized values. Every vector returned by `embed_texts`/`embed_query` (documents and queries alike) is L2-normalized manually (`_l2_normalize`) before use; a zero vector is returned unchanged rather than dividing by zero. Pinecone's `dotproduct` metric needs unit-length dense vectors to behave like cosine similarity.
- Text embedded: see "Contextual Embedding Text" below -- not the raw chunk text alone.

### Contextual Embedding Text
`DocumentIngestionService.process` in `trench_backend/app/service/document_ingestion_service.py` builds the text that's actually embedded and sparse-encoded as:
```
{document_name}
{heading_path}

{chunk_text}
```
with the `heading_path` line omitted entirely when it's empty (TXT uploads, or a section with no headers above it). This gives retrieval more context to match against than the bare chunk text alone. The Pinecone `"text"` metadata field -- what `generate()`/citations actually show the model and the user -- stays the raw, unprefixed chunk text; only the embedding/sparse-encoding input is contextualized.

### Sparse Encoding
`SparseEncodingService.encode_documents` in `trench_backend/app/service/sparse_encoding_service.py`:
- Uses pinecone-text's `BM25Encoder.default()` (pretrained, not fitted to corpus)
- Returns `dict[str, list]` (`SparseVector` type, `{"indices": [...], "values": [...]}`)
- Encodes the same contextualized text as embedding (see above), not the raw chunk text

### Pinecone Metadata and ID Scheme
`DocumentIngestionService.process` in `trench_backend/app/service/document_ingestion_service.py`:
- Chunk ID: `f"{document.id}:{index}"` (deterministic, not random)
- Metadata fields: `document_id` (str), `document_name` (str), `chunk_index` (int), `text` (str, raw chunk text), `heading_path` (str), `index_version` (int, `2`)
- Namespace: `personal_namespace(user_id)` or `company_namespace(tenant_id)` from `trench_backend/app/service/vector_store_service.py`
- Vectors stored: both dense (`values`) and sparse (`sparse_values`) in the same upsert, both scaled by `scale_hybrid` first (see below)

## Retrieval Path

### Dense + Sparse Combination
`RetrievalService.retrieve` in `trench_backend/app/service/retrieval_service.py`:
- Generates dense vector via `EmbeddingService.embed_query` (L2-normalized)
- Generates sparse vector via `SparseEncodingService.encode_query`
- Passes both to `vector_store.query` in `trench_backend/app/service/vector_store_service.py`, which scales them before querying -- see below
- Pinecone metric: `dotproduct` (required for hybrid)

### Hybrid Weighting (alpha)
`scale_hybrid` and `HYBRID_DENSE_WEIGHT` in `trench_backend/app/service/vector_store_service.py`:
- `HYBRID_DENSE_WEIGHT = 0.7` (alpha) -- how much weight the dense (semantic) vector gets relative to the sparse (BM25 keyword) vector in Pinecone's combined dotproduct score
- `scale_hybrid(dense, sparse, alpha)` scales the dense vector by `alpha` and the sparse vector's `values` by `1 - alpha` (its `indices` are left unchanged); `PineconeVectorStore.upsert` and `PineconeVectorStore.query` both call it with the same default alpha, so documents and queries are scaled identically -- hybrid search only makes sense when both sides are weighted the same way.

### Namespace Scheme and Scope Isolation
`trench_backend/app/service/vector_store_service.py`:
- Personal namespace: `f"personal:{user_id}"`
- Company namespace: `f"company:{tenant_id}"`
- Every query scoped to exactly one namespace via `KnowledgeScope.namespace` property
- Authorization enforced before retrieval: `RetrievalService.resolve_scope` validates tenant membership + role or explicit grant

### Candidate Count and Reranking
`hybrid_retrieve` in `trench_backend/app/graph/rag_graph.py`:
- Pinecone candidate count: `_RETRIEVAL_CANDIDATE_COUNT = 25`
- Rerank model: `rerank-v3.5` (Cohere) in `trench_backend/app/service/reranker_service.py`
- Final chunk count: `_FINAL_CHUNK_COUNT = 8`
- Reranker input: `chunk["content"]` for each candidate
- Reranker output: overwrites `chunk["score"]` with Cohere's `relevance_score`

### Score Meaning
- Before reranking: Pinecone hybrid-search score (dotproduct of scaled dense+sparse)
- After reranking: Cohere `relevance_score` (0-1 range, higher is more relevant)
- `assess_retrieval_sufficiency` and `_context_chunks` both read `chunk["score"]` expecting the Cohere value

## Query Handling

### Condense Query
`condense_query` in `trench_backend/app/graph/rag_graph.py`:
- Runs when `len(prior_messages) <= 1` is False (i.e., history beyond the current turn exists); otherwise returns the raw query unchanged for both `condensed_query` and `original_condensed_query`
- Model: `LLMClientService.resolve_platform_default` -- always the platform-owned Groq default, never the user's selected model or BYOK credential, since this is a cheap internal rewrite step that must not fail the turn or spend BYOK quota
- History sent: the last `MAX_HISTORY_MESSAGES` prior messages (excluding the current query)
- On resolution or generation failure: falls back to the raw query, unchanged, for both fields
- Sets `condensed_query` AND `original_condensed_query` to the same value; `original_condensed_query` is never touched again this turn

### Reformulate Query
`reformulate_query` in `trench_backend/app/graph/rag_graph.py`:
- Prompt built from `state["original_condensed_query"]` -- the original standalone question from `condense_query`, **never** `state["condensed_query"]` (which may already be a previous reformulation) -- so a second retry rewrites the same original question again rather than compounding drift
- Model: `LLMClientService.resolve_platform_default` (same rationale as `condense_query`)
- On resolution or generation failure: returns `{}` (retry proceeds with the unreformulated `condensed_query`)
- Writes only `condensed_query`, never `original_condensed_query`

### Retry Loop
`assess_retrieval_sufficiency` and `_route_after_sufficiency_check` in `trench_backend/app/graph/rag_graph.py`:
- Cap: `MAX_RETRIEVAL_ATTEMPTS = 2` total attempts (initial + 1 retry)
- Sufficiency rule: top chunk's Cohere score >= `RETRIEVAL_SUFFICIENCY_MIN_RERANK_SCORE = 0.3`
- Skip-retry rule: if the **latest** attempt's top score is below `RETRIEVAL_SKIP_RETRY_BELOW = 0.05`, routes straight to `generate` even if under the attempt cap -- the namespace almost certainly has nothing relevant, so reformulating and retrying is very unlikely to help
- Best-attempt tracking: keeps `best_retrieved_chunks` (the attempt with the highest top score so far, not necessarily the latest one) and `last_retrieval_top_score` (the latest attempt's own top score, used only for the skip-retry check)
- When attempts run out (or the skip-retry rule fires): proceeds to `generate` with `best_retrieved_chunks`, even if insufficient

## Generation

### No-Relevant-Context Floor
`_context_chunks` in `trench_backend/app/graph/rag_graph.py`:
- Pinecone (and then Cohere's rerank) almost always return *something* from a non-empty namespace, even when nothing retrieved is genuinely relevant -- a non-empty `best_retrieved_chunks` is therefore not by itself evidence the question is answerable
- `_context_chunks(state)` returns only the chunks from `best_retrieved_chunks` scoring at or above `MIN_USABLE_RERANK_SCORE = 0.1`, in their original order
- Used by **both** `generate` (prompt numbering and the empty/no-context decision) and `build_citations` (validating `[n]` markers against this same list's length), so a chunk's position here is the single source of truth for its citation number in both places -- they can never drift into two different ideas of "chunk 2"
- If every chunk is below the floor (or the list is empty), `generate` treats the turn as having no relevant context, and `build_citations` returns `[]`

### System Prompt Cases
`generate` in `trench_backend/app/graph/rag_graph.py` picks one of three system prompts, and logs which (`prompt_case`) plus the context chunk count on every turn:

1. **`empty_kb`**: `state["knowledge_base_empty"]` is True
   - Uses `_EMPTY_KNOWLEDGE_BASE_SYSTEM_PROMPT`
   - Tells the model the scope has zero documents (not "nothing matched")
   - Suggests uploading documents

2. **`no_context`**: `state["knowledge_base_empty"]` is False AND `_context_chunks(state)` is empty
   - Uses `_NO_RELEVANT_CONTEXT_SYSTEM_PROMPT`
   - Tells the model a search ran but found nothing relevant
   - Explicitly forbids citation markers

3. **`normal`**: `_context_chunks(state)` is non-empty
   - Uses `_BASE_SYSTEM_PROMPT`
   - Includes numbered context passages (from `_context_chunks`, not the unfiltered `best_retrieved_chunks`)
   - Requires citations for factual claims

### Context Formatting
`generate` in `trench_backend/app/graph/rag_graph.py`:
- Format: `"[{i + 1}] (from {chunk['document_name']}) {chunk['content']}"`, built from `_context_chunks(state)`
- Numbering: 1-based, matching prompt order
- Separated by `"\n\n"`

### Conversation History
`generate` in `trench_backend/app/graph/rag_graph.py`:
- Reads `state["messages"]` (restored from checkpointer)
- Converts LangChain `BaseMessage` to `{"role": "user"/"assistant", "content": "..."}`
- Windowed to the last `MAX_HISTORY_MESSAGES` prior messages (excluding the current turn's own query) -- an unbounded history would otherwise make every turn resend the whole conversation, growing token cost and latency without bound as a thread gets long. The full history is still what's persisted (`ChatHistory`/`Thread` rows); only what's sent to the model here is capped.
- Current turn's query appended last: `[*history, {"role": "user", "content": state["query"]}]`
- `condense_query` windows its own history the same way (same `MAX_HISTORY_MESSAGES` constant)

### Model/Credential Resolution
`LLMClientService.resolve_for_knowledge` in `trench_backend/app/service/llm_client_service.py` (used only by `generate` -- `condense_query`/`reformulate_query` always use `resolve_platform_default` instead, see "Query Handling" above):
- Personal knowledge: uses the user's selected model + personal BYOK credential (Groq or the user's own key)
- Company knowledge: uses the user's selected model + the tenant's shared credential (if set), otherwise the platform default (Groq)
- Missing credential (personal): raises `MissingCredentialError`, surfaced as an error to the user
- Missing credential (company): silently falls back to the Groq platform default

## Citations

### Citation Production
`build_citations` in `trench_backend/app/graph/rag_graph.py`:
- Extracts `[n]` markers via regex `_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")`
- Filters to valid range: `1 <= n <= len(_context_chunks(state))`
- Deduplicates (set comprehension)
- Sorts ascending
- Returns `[]` immediately if `_context_chunks(state)` is empty (no-context turn), regardless of what markers appear in the response

### Citation Number Mapping
- `citation_number` equals the 1-based position in `_context_chunks(state)` -- **not** `best_retrieved_chunks` directly, since a chunk below `MIN_USABLE_RERANK_SCORE` is filtered out of both the prompt and citations
- Matches the numbering `generate` put in the prompt: `[{i + 1}]`
- The frontend must match by this number, not by position in the returned list

### Invalid/Missing Markers
- Markers outside the 1-N range: dropped (silently ignored)
- No markers in the response: returns an empty citation list
- Duplicate markers: deduplicated, only one citation kept

## Output Guardrail

`check_output_guardrail` in `trench_backend/app/graph/rag_graph.py` and `GuardrailService.check_output` in `trench_backend/app/service/guardrail_service.py`:

### What It Redacts
Credential-shaped substrings matching any of (each a precompiled regex in `_SECRET_PATTERNS`, deliberately narrow to a real provider's documented key shape to keep false positives low):

| Pattern | Covers |
|---------|--------|
| `sk-[A-Za-z0-9_-]{20,}` | OpenAI / Anthropic |
| `AIza[A-Za-z0-9_-]{30,}` | Google |
| `gsk_[A-Za-z0-9]{20,}` | Groq |
| `gh[pos]_[A-Za-z0-9]{30,}` | GitHub (personal/OAuth/server tokens) |
| `github_pat_[A-Za-z0-9_]{20,}` | GitHub (fine-grained PAT) |
| `AKIA[0-9A-Z]{16}` | AWS access key id |
| `xox[baprs]-[A-Za-z0-9-]{10,}` | Slack |
| `-----BEGIN [A-Z ]*PRIVATE KEY-----...-----END [A-Z ]*PRIVATE KEY-----` (DOTALL) | PEM private key blocks, any key type |
| `scheme://user:password@host` | A URL with a plaintext password embedded in its userinfo component |

- Redaction replacement: `[REDACTED]`
- Flags recorded: `f"redacted a credential-shaped match for {pattern.pattern!r}"`
- There is no longer an input guardrail -- see the Overview section above

### Streaming Limitation
`check_output_guardrail` runs after `generate` has already streamed each token live via `stream_manager.append_chunk(..., {"type": "token", ...})`. The guardrail protects the persisted `ChatHistory` record and the final "done" event's content, but cannot retroactively un-stream tokens the client already received. Closing this fully would require buffering the entire response before streaming any of it, which trades away streaming itself.

## Streaming and Persistence

### Token Streaming Path
`generate` in `trench_backend/app/graph/rag_graph.py`:
- Calls `LLMClientService.stream_generate` for each delta
- Appends to `stream_manager` immediately: `stream_manager.append_chunk(stream_id, {"type": "token", "content": delta})`
- `stream_manager` in `trench_backend/app/utils/stream_manager.py` fans out to all SSE subscribers

### SSE Event Types and Payloads
`sse_generator` in `trench_backend/app/utils/sse.py`:

- **token**: Streaming text delta
  ```
  event: token
  data: {"content": "Hello"}
  ```

- **error**: Error message (access denied, missing credential, etc.)
  ```
  event: error
  data: {"content": "You don't have access to this tenant's company knowledge"}
  ```

- **interrupted**: Partial content when stream cancelled
  ```
  event: interrupted
  data: {"content": "Partial response..."}
  ```

- **done**: Final response with citations
  ```
  event: done
  data: {"content": "Full response text", "citations": [...]}
  ```

### Persistence
`persist` in `trench_backend/app/graph/rag_graph.py`:
- Updates `ChatHistory` row: `content`, `chunks` (citations), `status` ("completed" or "failed")
- Status "failed" when `state["access_denied"]` is True
- Sends final "done" event via `stream_manager.append_chunk`
- Calls `stream_manager.finish(stream_id)` to mark the stream done and schedule eviction

### Cancellation
`ChatService.cancel` in `trench_backend/app/service/chat_service.py`:
- Calls `stream_manager.cancel_task(stream_id)` which calls `task.cancel()`
- `ChatService._run_generation` catches `asyncio.CancelledError`
- Persists partial content + status="interrupted" in a fresh DB session
- Appends "interrupted" event to stream_manager
- Best-effort: records the partial response into checkpoint state via `aupdate_state` for conversational memory

### Checkpointer Storage
`get_checkpointer` in `trench_backend/app/database/checkpointer.py`:
- Uses LangGraph's `AsyncPostgresSaver` with a psycopg connection pool
- Pool size: `CHECKPOINTER_POOL_SIZE = 10`
- Stores: graph state (messages, node outputs) per `thread_id`
- Tables: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` (managed by LangGraph's `.setup()`)
- Separate from the app's SQLAlchemy engine (own connection pool)

## Request-Level Limits

`trench_backend/app/schemas/chat.py` and `trench_backend/app/service/rate_limit_service.py`, enforced before any node in the graph runs:

- **Query length**: `ChatMessageRequest.query` is capped at 4000 characters (`max_length=4000`); a longer query is rejected with 422 by Pydantic validation.
- **Rate limit**: `require_chat_rate_limit` caps each user to `MAX_REQUESTS_PER_WINDOW = 20` calls to `POST /chat/threads/{thread_id}/messages` per `WINDOW_SECONDS = 60`, in-memory, fixed-window. Over the limit returns 429 with a `Retry-After` header (seconds until the window resets). In-memory state isn't shared across worker processes -- fine for a single-worker deployment; a multi-worker deployment would need this backed by Redis (`INCR` + `EXPIRE`) instead (see the module's own TODO).

## Document Deletion

`DocumentService._delete_document_row` in `trench_backend/app/service/document_service.py`:
- Deletes Pinecone vectors by **id prefix** (`f"{document.id}:"`, via `PineconeVectorStore.delete_by_prefix` -- list-then-delete, paginated at up to 1000 ids per batch), not by reconstructing `f"{id}:{index}"` for `range(document.chunk_count)`. `chunk_count` can drift from what's actually in Pinecone (a partial or retried ingestion), so trusting it risks leaving orphaned vectors behind; prefix-based deletion always removes everything that was ever upserted for the document regardless of what Postgres currently believes `chunk_count` is.
- This always runs, not gated on `chunk_count > 0`.

`AuthService.delete_account` in `trench_backend/app/service/auth_service.py`:
- Best-effort wipes the caller's personal Pinecone namespace (`PineconeVectorStore.delete_namespace`, `delete_all=True` scoped to that namespace) before deleting the Postgres row. A failed namespace wipe is logged but never blocks account deletion -- one orphaned namespace is a far smaller problem than an account the user can never delete.
- There is no `delete_tenant` feature anywhere in this codebase, so there's no equivalent company-namespace cleanup path for tenant deletion to attach to.

## Failure Modes

| Failure | What code does | What user sees |
|---------|-----------------|----------------|
| Missing credential (personal BYOK) | `generate` catches `MissingCredentialError`, writes error to stream, returns error as response | SSE "error" event with message "Your selected model requires a {provider} API key. Add one in Settings, or switch to a different model." |
| Pinecone error (in `hybrid_retrieve`, `vector_store_service.py`, or anywhere else in the graph) | No node wraps its own Pinecone/Cohere call in a try/except, deliberately -- `ChatService._run_generation` wraps the entire `ainvoke()` of this graph in one outer try/except. Any uncaught exception from any node is caught there, persisted as the `ChatHistory` row's `status="failed"` with the exception's message as content | SSE "error" event with the exception's message |
| Cohere rerank error | Same outer-exception handling as above (no node-local try/except) | SSE "error" event with the exception's message |
| LLM generation error | `ChatService._run_generation` catches `Exception`, persists `status="failed"`, sends "error" event | SSE "error" event with exception message |
| Empty scope (no company access) | `validate_knowledge_access` sets `access_denied=True`, routes to `access_denied` node | SSE "error" event with "You don't have access to this tenant's company knowledge" |
| Query too long (>4000 chars) | Rejected by Pydantic validation before any node runs | 422 response, never reaches the graph |
| Rate limit exceeded (>20/min) | Rejected by `require_chat_rate_limit` before any node runs | 429 response with `Retry-After` header, never reaches the graph |
| Cancelled stream | `ChatService._run_generation` catches `CancelledError`, persists partial content + `status="interrupted"`, sends "interrupted" event | SSE "interrupted" event with partial content |
| Document ingestion failure | `DocumentIngestionService.process` catches `Exception`, sets `status="failed"`, `error_message=exc` | Document row shows `status="failed"` with error message |

Adding a node-local try/except around an individual Pinecone/Cohere call would only duplicate `ChatService._run_generation`'s outer handling with a different message, not add any real protection -- see `rag_graph.py`'s own module docstring.

## Tunable Constants

| Name | Value | File | Effect |
|------|-------|------|--------|
| `MAX_RETRIEVAL_ATTEMPTS` | 2 | `trench_backend/app/graph/rag_graph.py` | Hard cap on the retrieval-reformulate retry loop (initial attempt + 1 retry) |
| `RETRIEVAL_SKIP_RETRY_BELOW` | 0.05 | `trench_backend/app/graph/rag_graph.py` | If the latest attempt's top rerank score is below this, skip straight to `generate` instead of reformulating/retrying -- the namespace almost certainly has nothing relevant |
| `MAX_HISTORY_MESSAGES` | 10 | `trench_backend/app/graph/rag_graph.py` | Most recent prior messages (excluding the current query) sent as history to `condense_query` and `generate` |
| `RETRIEVAL_SUFFICIENCY_MIN_RERANK_SCORE` | 0.3 | `trench_backend/app/graph/rag_graph.py` | Minimum top chunk Cohere rerank score to consider retrieval sufficient |
| `MIN_USABLE_RERANK_SCORE` | 0.1 | `trench_backend/app/graph/rag_graph.py` | Minimum Cohere rerank score for a chunk to count as usable context at all (floor below `RETRIEVAL_SUFFICIENCY_MIN_RERANK_SCORE`) |
| `_RETRIEVAL_CANDIDATE_COUNT` | 25 | `trench_backend/app/graph/rag_graph.py` | Number of hybrid candidates pulled from Pinecone before reranking |
| `_FINAL_CHUNK_COUNT` | 8 | `trench_backend/app/graph/rag_graph.py` | Number of chunks after Cohere reranking passed to generation |
| `CHUNK_SIZE` | 512 | `trench_backend/app/service/chunking_service.py` | Maximum tokens per chunk (measured in cl100k_base tokens) |
| `CHUNK_OVERLAP` | 80 | `trench_backend/app/service/chunking_service.py` | Token overlap between adjacent chunks |
| `DIMENSIONS` | 768 | `trench_backend/app/service/embedding_service.py` | Embedding vector dimensionality (gemini-embedding-001 with Matryoshka truncation); vectors are L2-normalized manually after the API call |
| `HYBRID_DENSE_WEIGHT` | 0.7 | `trench_backend/app/service/vector_store_service.py` | Alpha weight given to the dense vector (sparse gets `1 - alpha`) in Pinecone's combined dotproduct score, applied identically at upsert and query time |
| `MAX_REQUESTS_PER_WINDOW` | 20 | `trench_backend/app/service/rate_limit_service.py` | Per-user chat message rate limit |
| `WINDOW_SECONDS` | 60 | `trench_backend/app/service/rate_limit_service.py` | Rate limit window |
| Query `max_length` | 4000 | `trench_backend/app/schemas/chat.py` | Maximum characters in a chat query (422 if exceeded) |
| `CHECKPOINTER_POOL_SIZE` | 10 | `trench_backend/app/database/checkpointer.py` | Postgres connection pool size for the LangGraph checkpointer |
| `DEFAULT_EVICTION_DELAY_SECONDS` | 300.0 | `trench_backend/app/utils/stream_manager.py` | Seconds after stream finish before buffer eviction |

## Re-indexing (index_version=2)

The embedding/chunking/weighting changes above (L2 normalization, hybrid alpha weighting, heading-path-aware contextual chunking) change what gets written to Pinecone going forward -- vectors indexed before these changes don't have the new `heading_path`/`index_version=2` metadata, aren't normalized, and weren't scaled by `scale_hybrid`. New documents uploaded after this change get the new treatment automatically; there is currently **no re-indexing script** to backfill documents ingested before it (out of scope for this change -- see the project's own tracking for when/whether a backfill is needed).

## Known Limitations

1. **In-memory stream_manager limits horizontal scaling**: `trench_backend/app/utils/stream_manager.py` states "Single-process only -- fine for one API worker; a multi-worker deployment would need this backed by something shared (Redis pub/sub, Postgres LISTEN/NOTIFY) instead."

2. **In-memory rate limiting has the same limitation**: `trench_backend/app/service/rate_limit_service.py`'s per-user counters aren't shared across worker processes either -- see its own module docstring and TODO.

3. **Output guardrail streaming limitation**: `trench_backend/app/graph/rag_graph.py` documents that the guardrail "cannot retroactively un-stream tokens the client already received" because `generate` streams each token live before `check_output_guardrail` runs.

4. **LlamaParse nested event loop quirk**: `trench_backend/app/service/parsing_service.py` notes that LlamaParse's internals "synchronously drive a second, nested event loop even from its documented async entry points," requiring workarounds with `nest_asyncio` and `asyncio.SelectorEventLoop`.

5. **No tenant-deletion namespace cleanup**: there is no `delete_tenant` feature in this codebase at all, so company-namespace Pinecone cleanup (the equivalent of what `delete_account` does for personal namespaces) has no code path to attach to yet.

## Working with the Pipeline

### Inspect the Graph
```bash
langgraph dev
```
- Uses configuration in `trench_backend/langgraph.json`
- Imports `app/graph/rag_graph.py:graph` (the uncompiled StateGraph instance)
- Renders the workflow visually in LangGraph Studio
- Note: Manual runs from Studio may fail due to missing `config["configurable"]` dependencies (db session, user, stream_id) - see `_config_get`'s docstring in `trench_backend/app/graph/rag_graph.py`

### Run Relevant Tests
```bash
pytest tests/test_rag_graph_citations.py
pytest tests/test_rag_graph_context_chunks.py
pytest tests/test_rag_graph_generate_prompts.py
pytest tests/test_rag_graph_guardrails.py
pytest tests/test_rag_graph_knowledge_availability.py
pytest tests/test_rag_graph_query_rewriting.py
pytest tests/test_rag_graph_reranking.py
pytest tests/test_rag_graph_sufficiency.py
pytest tests/test_rag_graph_topology.py
pytest tests/test_chat_flow.py
pytest tests/test_chat_limits.py
pytest tests/test_retrieval_service.py
pytest tests/test_reranker_service.py
pytest tests/test_chunking_service.py
pytest tests/test_embedding_service.py
pytest tests/test_vector_store_service.py
pytest tests/test_guardrail_service.py
pytest tests/test_document_service.py
pytest tests/test_document_ingestion_service.py
pytest tests/test_stream_manager.py
```

### Add a New Node
1. Define the async node function in `trench_backend/app/graph/rag_graph.py` with signature `async def node_name(state: AgentState, config: RunnableConfig) -> dict`
2. Add state fields to `trench_backend/app/graph/state.py` if needed (keep JSON-serializable)
3. Register the node in `build_graph()`: `graph.add_node("node_name", node_name)`
4. Wire edges in `build_graph()`:
   - For unconditional: `graph.add_edge("from_node", "node_name")`
   - For conditional: add a routing function, then `graph.add_conditional_edges("from_node", routing_function, {"label": "node_name", ...})`
5. If the node requires per-request dependencies (db, user, stream_id), read them via `_config_get(config, "key")`
6. If the node writes to stream_manager, use `stream_manager.append_chunk(state["stream_id"], {...})`
