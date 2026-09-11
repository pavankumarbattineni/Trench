# Trench — Personal Knowledge Base / Agentic RAG — Design Spec

Date: 2026-09-10
Status: Frozen — ready for implementation planning

## Purpose

A personal knowledge-base application: each user uploads their own documents,
which are parsed, chunked, embedded, and indexed, then queried through a
ChatGPT/Claude-style multi-thread conversational interface. Every user's
documents, embeddings, chunks, threads, and configuration are strictly
isolated from every other user's.

This builds on the existing Trench backend (FastAPI, async SQLAlchemy,
PostgreSQL, Firebase-backed auth with stateless JWT sessions) and follows its
established conventions: `router/service/database/utils/schemas/middleware`
layout, centralized error handling, encrypted-at-rest secrets via the
existing `TRENCH_CONFIG.MCP.encryption_key` (Fernet).

## Scope

In scope: document upload/processing pipeline, knowledge-base entity,
chunking (3 strategies), embeddings (local default + BYOK), vector storage
(Pinecone for both tiers — operator-owned project for free tier, user's own
credentials for BYOK), hybrid retrieval + reranking, LangGraph conversational
backend with Postgres checkpointing, thread management, auto-generated
titles, BYOK credential management with LangChain-based key validation,
free-tier usage limits.

Out of scope (later phases): multi-knowledge-base UI (entity exists now,
UI exposes one KB per user), OCR/image ingestion, additional vector store
providers beyond Pinecone, rate limiting/abuse prevention, query
rewriting/expansion beyond history-condensing, admin/observability tooling.

## Tiering model (the organizing principle)

Every configurable layer follows the same shape: a **free tier that runs on
infrastructure Trench operates** (no per-user key to configure), and a
**BYOK tier that unlocks a higher-quality or user-owned external provider**,
validated at credential-save time. Vector storage is the one layer where the
free tier is *also* the same external provider as BYOK (Pinecone), not
something self-hosted — see below for why.

| Layer | Free tier | BYOK |
|---|---|---|
| Parsing | LlamaIndex local readers (`SimpleDirectoryReader`) | LlamaParse |
| Embeddings | Local `BAAI/bge-small-en-v1.5` (384-dim) via `fastembed` | OpenAI `text-embedding-3-*`, Cohere |
| Vector store | Pinecone, operator-owned project/index | Pinecone, user's own project/index |
| Reranker | Local `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cohere Rerank |
| LLM | Groq (operator-provided key) | OpenAI / Anthropic / Gemini |
| Background jobs | procrastinate (Postgres-based queue) | — |

**Embeddings are never stored in PostgreSQL, full stop** — not even for the
free tier. Postgres holds structured metadata and chunk text only
(`document_chunks.content`); the embedding vector itself always lives in
Pinecone, keyed by the chunk's own UUID. This ruled out pgvector as the
free-tier vector store (pgvector's entire mechanism is storing the vector
inside a Postgres column), which is why free tier uses an operator-owned
Pinecone project instead: one `VectorStoreProvider` implementation
(`PineconeProvider`) serves both tiers, just pointed at different
credentials/project depending on whether the user has BYOK'd their own.

All provider access — free and BYOK alike — goes through LangChain's chat
model classes (`ChatGroq`, `ChatOpenAI`, `ChatAnthropic`,
`ChatGoogleGenerativeAI`), since LangGraph already builds on LangChain's
model abstractions. No second abstraction layer (e.g. LiteLLM) alongside it.

## New entity: KnowledgeBase

Sits between User and Document. One KnowledgeBase is auto-created per user
on first use in v1 (UI never shows the concept yet — this is purely a schema
decision that avoids an undefined migration path later).

**Why**: embedding vectors from two different models are not comparable —
silently mixing them in one retrieval corpus returns wrong results with no
error. Rather than support in-place embedding-model changes (which requires
a full re-embed-and-reindex migration feature), the embedding config is
**immutable once the first document is indexed under a KnowledgeBase**. A
user who wants a different embedding model creates a new KnowledgeBase
(cheap, and the one clean way to sidestep the migration problem). Multi-KB
becomes a pure UI addition later with zero backend rework.

## Database schema

```
knowledge_bases
  id, user_id, name, embedding_provider, embedding_model,
  embedding_dimensions, vector_store_provider, vector_store_ref,
  is_locked, created_at

documents
  id, user_id, knowledge_base_id, filename, storage_path (see Document
  storage below),
  mime_type, file_size_bytes, content_hash (sha256), status, error_message,
  chunking_strategy, chunk_size, chunk_count,
  created_at, processed_at, updated_at

document_chunks
  id, document_id, knowledge_base_id, chunk_index, content, token_count
  -- no embedding column, ever -- the embedding lives only in Pinecone,
  -- keyed by this row's own id

user_credentials
  id, user_id, provider_type (llamaparse | openai_embed | openai_llm |
  anthropic_llm | gemini_llm | cohere_rerank | pinecone),
  encrypted_credential, validated_at, created_at

threads
  id, user_id, knowledge_base_id, title, retrieval_config (JSONB: top_k,
  rerank_enabled, llm_provider, llm_model), created_at, updated_at

usage_counters
  user_id, documents_uploaded_count   -- incremented on successful indexing
                                       -- only; never decremented on delete

-- LangGraph's own checkpointer tables (checkpoints, checkpoint_writes,
-- checkpoint_blobs) are separate and auto-managed; `threads` above owns
-- ownership/title/listing, LangGraph owns conversation state.
```

Content hash lets a re-upload of an identical file short-circuit to the
existing `documents` row instead of reprocessing and re-counting against the
free-tier limit.

## Configuration hierarchy

| Setting | Level | Locked after first document? |
|---|---|---|
| Embedding model + dimensions | KnowledgeBase | Yes |
| Vector store provider + credentials | KnowledgeBase | Yes |
| Chunking strategy + chunk size | Document | No — reprocessable per file |
| BYOK credentials (LlamaParse/embedding/LLM/rerank/Pinecone) | User | No |
| Top-K, rerank on/off, LLM provider/model | Thread (defaults from User) | No |

## Document storage

**Superseded:** originally planned as Firebase Storage. Firebase Storage
now requires the Blaze (pay-as-you-go) plan with a card on file even to
stay within free-tier usage, which conflicts with "no payment details."
Uploaded files are instead stored via a `DocumentStorageProvider`
abstraction (mirrors `VectorStoreProvider`), with a **local filesystem**
implementation for now (`trench_backend/storage/`, gitignored, path
configurable via `TRENCH_CONFIG.STORAGE.local_root_path`) — no signup, no
cost, no card. A cloud provider (Supabase Storage — free tier, no card
required at signup — or Firebase Storage once its billing policy is
revisited) can be added later as a second implementation without touching
any calling code.

## Document ingestion pipeline

Runs as a background job (procrastinate), never inline in the upload
request. States: `pending → parsing → chunking → embedding → indexing →
completed` / `failed` (with `error_message`).

Procrastinate's `App` holds its own connector (a `PsycopgConnector`,
separate from our SQLAlchemy engine) that must be explicitly opened
before `defer_async()`/a worker can use it. The FastAPI app opens it once
via `lifespan=` in `app/main.py` (closed on shutdown); tests open it via a
session-scoped autouse fixture in `conftest.py`, since httpx's
`ASGITransport` doesn't drive the ASGI lifespan protocol. Task modules
must be imported wherever `app/jobs/app.py` is loaded (API process, the
`procrastinate` CLI's `--app` flag, tests) for their `@app.task`-decorated
functions to register — done via an import at the bottom of `app.py`
itself rather than relying on each entrypoint to remember it.

1. **Upload**: validate file size cap and real content-type (sniffed, not
   just extension trust) before accepting; compute the content hash — if
   it matches an existing document for this user, short-circuit to that
   record (a re-upload of a file the user already has must never be
   blocked by the limit, since it consumes no new slot). Only for a
   genuinely new hash, check `usage_counters` limit (5 free documents)
   and fail fast with a friendly error before any processing starts.
2. **Parse**: LlamaIndex local readers by default; LlamaParse if the user
   has a validated LlamaParse credential on file for this request.
3. **Chunk**: per the document's own `chunking_strategy`/`chunk_size`
   (token-based sizing, not character-based).
4. **Embed**: per the owning KnowledgeBase's locked embedding config.
5. **Index**: upsert into Pinecone, namespaced to the KnowledgeBase
   (`document_chunks` holds chunk text/metadata in Postgres; Pinecone
   holds only the embedding vector, keyed by the chunk's id — never the
   reverse).
6. On success: increment `usage_counters.documents_uploaded_count`,
   mark `completed`. On failure at any stage: mark `failed`, store
   `error_message`, do not increment the counter.

Deleting a document removes its `document_chunks` rows and its vectors
from the vector store (cascade, not soft-delete-only) but never decrements
`documents_uploaded_count`.

## Vector store abstraction

```
VectorStoreProvider (protocol):
  upsert(namespace, vectors: list[(chunk_id, embedding, metadata)]) -> None
  query(namespace, embedding, top_k, filters) -> list[(chunk_id, score)]
  delete(namespace, chunk_ids) -> None
  delete_namespace(namespace) -> None
```

`PineconeProvider` implements this now (used for both the free and BYOK
tiers, pointed at operator vs. user credentials/project respectively); the
interface exists so a second provider could be added later without
touching the retrieval or ingestion code that calls it. Namespace is always
`kb_{knowledge_base_id}`, derived server-side from the authenticated user's
owned KnowledgeBase — never accepted as client input.

Operator-owned Pinecone credentials (for the free tier) live in
`TRENCH_CONFIG` (a new `PINECONE` section: `api_key`, `environment`/`cloud`
+ `region`, `index_name`), the same way `FIREBASE` credentials already do —
not in the database, since they're operational config, not a user's own
secret.

## Retrieval pipeline

```
query
  → condense (fold chat history + follow-up into a standalone search
     query — necessary for multi-turn threads; skipped on a thread's
     first turn)
  → parallel: dense vector search (top 30)  +  BM25 keyword search (top 30,
     via Postgres full-text search on document_chunks.content)
  → Reciprocal Rank Fusion (RRF) to combine the two rankings
  → fused top 20 candidates
  → cross-encoder rerank (local default, or Cohere Rerank if BYOK'd)
  → top-N (default N=5) → LLM context
```

Metadata filtering (document_id, date, tags) applies at the dense-search
stage, before fusion. Top-K/N are thread-level settings with the defaults
above.

## LangGraph architecture

**Nodes:**
1. `load_context` — fetch thread + KnowledgeBase + user config. Ownership
   is verified at the API/service layer *before* the graph is invoked —
   the graph itself is not the security boundary, consistent with how
   auth already works elsewhere in this app.
2. `condense_query` — cheap LLM call folding history into a standalone
   query; skipped on turn 1.
3. `retrieve` — runs the retrieval pipeline above.
4. `relevance_gate` (conditional edge) — if nothing retrieved clears a
   similarity floor, route to a "no relevant documents found" response
   node instead of forcing the LLM to answer from irrelevant chunks.
5. `generate` — LLM call with retrieved context, streamed token-by-token.
6. `persist` — checkpoint the turn; on turn 1, fire off async title
   generation (does not block the streamed response).

**Checkpointing**: LangGraph's `AsyncPostgresSaver`, same Postgres
instance, keyed by `thread_id`.

**Streaming**: `astream_events`/`astream(stream_mode="messages")` through
an SSE endpoint — the same token-by-token, reconnect-via-`Last-Event-ID`,
idempotent-stream-ID pattern already proven in ThinkLoop.

**Thread titles**: fire-and-forget cheap LLM call (Groq, short prompt,
small max_tokens) after the first exchange completes; writes
`threads.title` when done. Frontend shows a truncated-first-query
placeholder until it lands.

## Provider/model catalog (DB-driven, never hardcoded)

`providers` + `provider_models` tables hold every model Trench can use —
which one is the platform default for a given purpose (`llm` | `embedding`
| `rerank` | `parsing`), embedding dimensions, display names. Application
code never hardcodes a model name string; it always resolves through
`ProviderCatalogService.get_default(db, model_type)` or `.list_models(...)`.
A partial unique index enforces at most one `is_platform_default` row per
`model_type`. Seeded with a verified live snapshot (not a guess) of Groq's
current chat-capable models plus the local embedding/rerank defaults;
refreshable later via `ProviderCatalogService.sync_groq_models()` without
another migration. Operator-owned provider credentials that aren't a
user's own secret (Groq's key, for the free-tier LLM) live in
`TRENCH_CONFIG.GROQ.api_key`, the same pattern as `FIREBASE`.

## BYOK credential management

Every BYOK credential (LlamaParse, OpenAI/Anthropic/Gemini LLM keys,
OpenAI/Cohere embedding or rerank keys, Pinecone) is:

- **Validated at save time**, not per-request. For LLM providers
  specifically: each provider's native SDK issues a cheap `models.list()`
  call (matches the pattern proven in the abyss_backend reference
  codebase) — not LangChain, which has no uniform cheap "is this key
  valid" check across providers. LangChain is used later, only to build
  the chat model for actual generation. Any failure maps to a clear
  "that key looks invalid or expired" response, not a generic 500.
- **Encrypted at rest** via Fernet using `TRENCH_CONFIG.MCP.encryption_key`
  (already present in config, previously unused).
- **Decrypted only server-side**, only at the moment of constructing the
  provider client for an actual call — never returned to the frontend.

## Security

- Row-level ownership checks on every document/thread/KnowledgeBase query
  (`WHERE user_id = current_user.id`), server-derived, never client-supplied.
- Vector store namespace isolation as above.
- File upload validation: size cap + real content-type sniffing.
- Structured logging: no raw document content, no decrypted credentials —
  IDs and status only.
- Centralized error handling (existing `app/middleware/error_handler.py`)
  extends naturally to this feature's error paths — no new error-handling
  mechanism needed.

## Explicitly deferred (not this phase)

- Multi-KnowledgeBase UI.
- Additional vector store providers beyond Pinecone.
- OCR/image document support.
- Query rewriting/expansion beyond history-condensing.
- Rate limiting / abuse prevention on shared operator keys (Groq).
- Sentence-window retrieval, layout-aware PDF chunking.
