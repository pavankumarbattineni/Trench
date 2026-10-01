# Architecture Overhaul — Step 1 Change-Tracking Document

Date: 2026-10-01
Status: Analysis only — no code changed. Input to the architectural design phase.

## How to read this document

This is the output of Step 1 (full codebase review) for the planned overhaul:
(1A) strict multi-tenant auth, (1B) Agentic RAG via LangGraph, (1C) JEV
investigation, (2) Agentic RAG evaluation, (3) CI/CD + Docker. It records
**current state, required changes, and open questions** — no implementation
approach has been chosen yet, and no code has been touched.

Related prior spec: `docs/superpowers/specs/2026-09-10-agentic-rag-design.md`
(2026-09-09, "frozen — ready for implementation planning"). The actual code
diverged from it in several ways worth knowing before redesigning again:
no `KnowledgeBase` entity was built, no `document_chunks` Postgres table
exists (chunk text lives only in Pinecone metadata), no RRF fusion, no
cross-encoder reranker, no `relevance_gate` node, and procrastinate (named
in that spec as the job queue) was implemented then explicitly removed in
favor of raw `asyncio.create_task`. Treat that spec as historical context,
not as the ground truth for what exists today.

## Recommendation: this is five projects, not one

Per standard practice for a request this size, this should decompose into
independent specs/implementation cycles, each with its own design doc:

1. **Multi-tenant auth & isolation** (1A) — foundational; most other work
   depends on tenant identity existing first.
2. **Agentic RAG redesign** (1B + 1C) — depends on (1) for scoping if
   retrieval fan-out needs tenant-aware authorization at each new node.
3. **Evaluation framework** (2) — depends on (2) existing in some runnable
   form; can be designed in parallel but implemented after.
4. **CI/CD & Docker** (3) — largely independent; could even go first as
   low-risk, high-value groundwork, but is listed last per your instructions.
5. **(New, surfaced during review) Durable job queue + horizontal-scaling
   groundwork** — not explicitly requested, but both the RAG review and the
   general "production-ready and scalable" goal depend on it (see Section 4).
   Flagging as its own item rather than silently bundling it into #2.

Each should get its own brainstorming → design spec → implementation plan
cycle rather than one combined spec, per our usual process.

---

## Section 1: Multi-Tenant Auth & Isolation (Step 1A)

### 1.1 What already exists (do not rebuild)

| Component | Status |
|---|---|
| `Organization` entity (name, domain derived from creator email, owner_user_id) | Working |
| `OrganizationMember` (role: admin/member, **DB constraint: 1 org per user**) | Working |
| `KnowledgeAccess` grants (per-user company-knowledge authorization) | Working |
| Company-document scoping (`organization_id` FK + router deps) | Working |
| Vector-store namespace pattern `personal:{user_id}` / `company:{org_id}` | Working, generalizable |
| Three-tier roles: Trench app-admin, org-admin, org-owner | Working |
| `require_org_admin`/`require_org_member`/`require_company_knowledge_access` deps | Working, but applied per-route only |
| Domain-derivation utils (`extract_domain`, `is_public_email_domain` blocklist) | Working, but only invoked at org-creation time |
| Frontend: full org members/roles/knowledge-access RBAC UI | Working, needs little change |

This is a **recent, partial retrofit** (Alembic shows org/domain/owner_user_id
columns added 2026-09-10), not a mature core abstraction — most other tables
predate it and were never revisited.

### 1.2 Current flow vs. required change

| Router / Component | Current Flow | Required Change | Reason | Dependencies |
|---|---|---|---|---|
| Auth (signup/login) — `router/auth.py`, `auth-service.ts`, `signup/page.tsx` | Any email (personal or company) can sign up via Firebase; no domain check at signup/login; no org assigned | Enforce company-domain-only at signup, before Firebase account creation (frontend) and again server-side (backend); decide invite-only vs. auto-provision org by domain | Multi-tenancy requirement | Decision on signup model (see open questions) |
| `users` table | No `organization_id`/tenant FK; tenant only derivable via `OrganizationMember` join (0 or 1 row) | Likely add direct `organization_id` on `User`, eventually NOT NULL | Needed for every scoping query; simplifies auth | Depends on mandatory-org-at-signup decision |
| JWT / session — `auth_service.py`, `security.py` | JWT carries only `user_id`, no tenant claim; no server-side session store | Consider embedding `organization_id` in JWT, or making tenant resolution a cached mandatory step of `get_current_user` | API-level tenant validation | — |
| `deps.py` | Tenant-scoped checks (`require_org_admin` etc.) require caller-supplied `organization_id`; no implicit "current tenant" dependency | Add `get_current_tenant`/`require_tenant_context` derived from the authenticated user, applied broadly (router-level `dependencies=[...]`) rather than per-route opt-in | Currently missing/inconsistent on `/documents`, `/threads`, `/credentials`, `/users` | New dependency + router-wide application |
| `documents` table/router | `organization_id` nullable; personal docs have no tenant dimension at all; personal vector namespace keyed by user, not tenant | Decide whether personal docs become tenant-scoped (`organization_id` NOT NULL even for personal, or namespace prefixed by org) | "Document-level isolation," tenant-wide export/delete | Conflicts with current dual unique-constraint design on `Document` (see 1.4) |
| `threads` / `chat_history` tables | No `organization_id` at all; LangGraph checkpoints keyed only by `thread_id`, with no tenant dimension | Add `organization_id` to `Thread` for audit/compliance; checkpointer isolation stays delegated to router-level ownership checks (LangGraph itself has no tenant hook) | "Conversation/session isolation," "agent state isolation" | — |
| `user_credentials` (BYOK) | Per-user only; single **global** Fernet root key (purpose-derived, not per-tenant) | Decide: org-shared BYOK credentials (admin-managed) vs. keep per-user; if strict isolation blast-radius matters, consider per-tenant encryption key material | "Preventing cross-tenant data access," security hardening | Product decision — not purely technical |
| `usage_counters` | Per-user only, no org-level quota | Decide if quotas should be org-wide (common SaaS billing model) | Depends on billing model (not yet discussed) | — |
| `main.py` / middleware | No tenant-resolution middleware; CORS origins hardcoded; no tenant_id in structured logs | Add tenant context early (middleware or dependency) + tenant_id in logs for incident triage; make CORS config-driven | "API-level tenant validation," ops/debuggability | — |
| DB-level enforcement | 100% application/service-layer; no Postgres RLS; document fetch-by-ID has no DB-level tenant check | Consider Postgres RLS as defense-in-depth for "database-level tenant isolation" requirement | Explicit ask in your brief | Bigger lift — worth scoping separately |
| Frontend: `create-organization-form.tsx` | Org domain opportunistically derived from creator's current email, no verification (a personal Gmail user could create an org scoped to `gmail.com`) | Redesign so domain is validated/pre-provisioned, not inferred after the fact | Fundamental conflict with "company-domain-only" | Depends on backend signup-model decision |
| Frontend: "Trench application admin" bootstrap (`getAdminStatus`, signup toggle) | First-ever signup can become a global app-admin who can create any number of orgs; conflates app-wide bootstrap with per-tenant provisioning | Needs explicit decision: does this survive, or does onboarding become fully self-service / per-tenant? | Architectural ambiguity | — |
| Frontend: `googleProvider` (`firebase.ts`) | No `hd` (hosted domain) hint; accepts any Google account | Add `hd` hint as UX improvement; real enforcement must stay server-side | Multi-tenancy | — |

### 1.3 Structural DB blockers worth flagging now

- `OrganizationMember.UniqueConstraint("user_id")` enforces **at most one org
  per user** at the DB level. This *simplifies* strict single-tenant
  isolation today but is the single biggest blocker if multi-org membership
  is ever wanted later (e.g., consulting/contractor access to two tenants).
- `users.email` and `users.username` are **globally unique**, not
  tenant-scoped. Fine if email remains the universal login identity; blocks
  any future "same email could exist as separate accounts in two tenants"
  model.
- `Document` has two independent unique constraints — `(user_id,
  content_hash)` and `(organization_id, content_hash)` — that only work
  today because `organization_id` is nullable for personal docs (Postgres
  treats NULLs as distinct). If personal docs become tenant-scoped
  (`organization_id` NOT NULL), this dedup logic needs to be redesigned
  (e.g., one constraint on `(organization_id, scope, content_hash)`).
- No composite `(organization_id, id)` index pattern exists anywhere — "fetch
  by id AND verify tenant" is done in service code, not enforced by an
  index/constraint. A service-layer bug is the only thing currently
  preventing cross-tenant access by primary-key guessing.

### 1.4 Decisions (made 2026-10-01)

1. **Onboarding model: invitation-based, not self-provisioning.** Only an
   org Owner can register and create an org (providing org details +
   verifying their company email/domain at signup). There is no
   "auto-provision by domain" or "any matching-domain user can self-join"
   path — employees join only via an email invitation sent by an
   Owner/Admin, never by independently signing up and being auto-matched
   to an existing org by domain. This **replaces** the current
   `create-organization-form.tsx` self-service flow and the "auto-add
   existing same-domain users" behavior in `OrganizationService.create`.
2. **Org is mandatory for every employee, with no orgless/personal-only
   mode.** Every account belongs to exactly one organization. However,
   "personal knowledge base" still exists as a concept — it's just no
   longer available to a user with no organization at all; it's a private,
   per-employee space *within* their org membership, not an alternative to
   having an org.
3. **One org per user — permanent.** Confirmed: keep the existing DB
   constraint (`OrganizationMember.UniqueConstraint("user_id")`) as-is. No
   multi-tenant-membership schema work needed.
4. **Three roles, not two: OWNER / ADMIN / MEMBER** (today's code only has
   org-admin/org-member via `OrganizationMember.role`, with `owner_user_id`
   as a separate, less-enforced concept on `Organization`). New rules:
   - **OWNER**: invite/add employees, upload documents, manage knowledge
     bases, grant/revoke KB access, promote Member→Admin, remove any
     Admin or Member, manage the org. Cannot be removed or demoted by
     anyone.
   - **ADMIN**: invite/add employees, upload documents, manage knowledge
     bases and their access, remove Members only. Cannot remove/manage the
     Owner or other Admins, cannot self-promote.
   - **MEMBER**: default role for every invited employee; query access to
     org KB (unless revoked) and own personal KB; no management
     permissions.
   - Role is **never client-selectable at registration/invitation accept
     time** — the backend determines it (Owner at org-creation, Member on
     invite-accept, promotion only via an explicit Owner action).
5. **Knowledge-base access is managed independently of org role** — this
   already exists as the `KnowledgeAccess` grant table; it needs to be
   confirmed/extended to cover the new role model (Owner/Admin get implicit
   access, same as today; Members need/can-lose an explicit grant, same as
   today — this part of the existing design already matches the new
   requirement closely).
6. **Dual knowledge base per employee, confirmed**: every employee gets (a)
   the shared org KB (default access, revocable per-employee by
   Owner/Admin) and (b) a private personal KB no other user can access
   (future sharing mechanism explicitly out of scope for now). This maps
   directly onto the existing `personal:{user_id}` / `company:{org_id}`
   Pinecone namespace split — no new namespace scheme needed, just
   stricter enforcement that personal KB still requires org membership to
   exist at all.
7. **Strict cross-org isolation confirmed**: one org can never access
   another org's knowledge base (matches existing `company:{org_id}`
   namespace design; must hold even as retrieval becomes agentic/fan-out
   in Section 2).
8. **BYOK credential resolution (new logic needed)**:
   - Default: both org KB and personal KB use the system default model
     (today's Groq platform default).
   - If a Member or Admin supplies their own API key, it applies **only to
     that individual's personal KB** — never to the shared org KB.
   - If the **Owner** supplies an API key, it applies **to the org KB for
     every role** (Owner/Admin/Member all use the Owner's key when querying
     org knowledge) — a genuinely new capability; today `UserCredential` is
     per-user only with no concept of "this credential powers a shared
     resource for other users." Needs a schema change (see 1.5).
   - Login flow has no role selection UI ("Login as Admin" etc.) — role is
     always resolved server-side after authentication, never chosen by the
     user. (Current code already does this — no explicit role picker exists
     today — but worth stating as a hard constraint for the invite-accept
     flow too.)

### 1.5 Further decisions (made 2026-10-01, round 2)

9. **Email delivery: SMTP**, not a third-party transactional-email API
   (Resend/SES/SendGrid/Postmark all declined). Both org-invite emails and
   password-reset emails go through SMTP.
10. **Password reset becomes fully custom end-to-end**: the backend
    generates and validates its own reset token and updates the password
    via the Firebase Admin SDK, replacing Firebase's own
    `sendPasswordResetEmail`/`oobCode`/`verifyResetCode` client flow
    entirely. This touches `auth-service.ts` (`requestPasswordReset`,
    `verifyResetCode`, `completePasswordReset`), `forgot-password/page.tsx`,
    `reset-password/page.tsx` + `reset-password-form.tsx`, and needs new
    backend endpoints + a reset-token table (or signed token, see design
    spec). Firebase remains the password *store*, just not the
    reset-flow/email owner.
11. **Invited employee must authenticate with the exact invited email
    address** — reject if a different email is used at invite-accept,
    including via Google sign-in.
12. **Domain verification: keep current level for now** (email-derived
    domain + public-provider blocklist, no DNS/MX check). Stronger
    verification (DNS TXT) is explicitly deferred to a future phase, not
    this one.
13. **Bulk employee onboarding**: the Organization page gets a bulk
    upload (CSV/Excel) option with a predefined column set including a
    **Role** column (Owner assigns roles; for individual single-add, the
    inviter picks the role per employee too — subject to each role's
    permission limits, e.g. an Admin can only assign Member). All newly
    added users (individual or bulk) receive an email invitation
    automatically.
14. **Existing data: full wipe, confirmed.** All current users are deleted
    from both Postgres and Firebase before the new invitation-based system
    goes live — explicitly confirmed as acceptable (current data is
    test/throwaway). **This is a destructive, irreversible step** and will
    be called out again for explicit re-confirmation immediately before
    execution in the implementation plan — it is recorded here as a
    decision, not yet performed.

### 1.6 Remaining open questions

1. **BYOK org-key schema**: storing "the Owner's credential, used on behalf
   of the whole org for company-KB queries" is a new relationship — I'm
   leaning toward a new `organization_credentials` table (parallel to
   `user_credentials`) for clarity on revocation/ownership; will propose
   this concretely in the design spec rather than asking here.
2. **Invitation token expiry/resend**: what expiry window for an invite
   link (proposing 7 days as a default), and should an Owner/Admin be able
   to resend/revoke a pending invite? (Will propose a default in the spec;
   flag if you want something different.)
3. **SMTP provider/credentials**: is there an existing SMTP account to use
   (e.g. company Google Workspace SMTP, an existing transactional SMTP
   relay), or does this need to be provisioned as part of the work?
4. Is Postgres RLS in scope for this phase, or is thorough
   application-layer enforcement (consistent dependency + service-layer
   checks, now with a clearer role model) sufficient for now?
5. Do we need per-tenant custom domains/subdomains for routing, or is a flat
   `/api/v1/...` URL space with JWT-derived tenant fine indefinitely?

---

## Section 2: Agentic RAG Redesign (Step 1B)

### 2.1 Current state (accurate as of code review, not the frozen spec)

The current LangGraph graph (`app/graph/rag_graph.py`) is a **linear
retrieve-then-generate pipeline with exactly one conditional branch**
(access-control allow/deny):

```
load_thread_state (no-op) → validate_knowledge_access
  --denied--> access_denied → persist → END
  --allowed--> condense_query → hybrid_retrieve → generate → build_citations → persist → END
```

| Agentic RAG capability | Exists today? |
|---|---|
| Query rewriting/condensation | Partial — single-shot standalone rewrite, no quality check |
| Query decomposition / multi-query | No |
| Planner node | No |
| Retrieval strategy selection | No — always one hybrid Pinecone query, fixed top_k=8 |
| Dedicated retrieval agent / tool-calling retrieval | No |
| Reranking | No — raw Pinecone hybrid score order used as-is |
| Web search fallback | No |
| Groundedness/faithfulness checking | No |
| Retry / self-correction loop | No |
| Confidence estimation | No |
| Hybrid dense+sparse retrieval | Yes — Pinecone dotproduct, Gemini dense + generic BM25 sparse |
| Conversational memory | Yes — `messages` reducer + Postgres checkpointer, thread-scoped |
| Streaming | Yes — custom in-process SSE, single-worker only |
| Access control before retrieval | Yes — single documented choke point (`RetrievalService.resolve_scope`) |

### 2.2 Current flow vs. required change

| Component | Current Flow | Required Change | Reason | Dependencies |
|---|---|---|---|---|
| `app/graph/rag_graph.py` | Linear retrieve→generate | Redesign into planner/retrieval-strategy/validation graph (see Section 2.3 for proposed shape — to be confirmed with you before design doc) | Agentic RAG requirement | — |
| `RetrievalService.resolve_scope` | Single documented choke point, called once per turn | Must remain the **sole** entry point even as new agentic paths (multi-query, parallel sub-queries, reranking) are added, or tenant isolation erodes | Ties RAG redesign to Section 1 isolation guarantees | Section 1 (tenant model) |
| Retrieval (`retrieval_service.py`) | Single hybrid query, fixed top_k=8, no reranking, no retry-on-empty | Add reranking, adaptive/retry retrieval, possibly multi-query | Core of the Agentic RAG ask | — |
| `generate` node | All retrieved chunks go into prompt unconditionally, no groundedness check before persisting | Add context-relevance filtering + groundedness/faithfulness validation node | Explicit ask (Step 2 evaluation dimensions imply this must exist to be measurable) | — |
| Checkpointer (`checkpointer.py`) | Keyed purely by `thread_id`; isolation enforced only at router (`ThreadService.get_owned`), not in the checkpointer itself | New entry points (eval harness, retries from other services, future multi-agent sub-graphs) must re-assert ownership at every call site, or tenant/user identity needs folding into the checkpoint key | Ties to "agent state isolation" ask | Section 1 |
| Vector store (`vector_store_service.py`) | One shared Pinecone index, isolation by namespace string convention only | Any new agentic retrieval path (parallel sub-queries, reranking re-embeds) must not construct namespaces independently | "Vector database isolation" ask | Section 1 |

### 2.3 On the proposed node list in your brief

You listed candidate nodes (query understanding, classification, planning,
retrieval strategy selection, vector DB + web search, rewriting, multi-query,
reranking, relevance evaluation, groundedness checking, generation,
validation, retry loop, confidence estimation). I'd rather propose a
concrete, trimmed graph **after** we resolve the JEV question (Section 3) and
you confirm the evaluation dimensions in Section 2's parent (Step 2), since
the planner's design and what gets measured should be designed together —
proposing the graph now risks designing it twice. This will be the first
thing addressed in the Agentic RAG design-doc phase, not deferred
indefinitely.

### 2.4 Open questions (1B)

1. Is a web-search tool actually needed, or is retrieval scope permanently
   limited to the tenant's own ingested documents? (Affects whether
   "planner picks a tool" is a real decision point or over-engineering for
   this product.)
2. Can a single conversation ever need to blend "personal" and "company"
   knowledge in one answer (multi-source retrieval), or is a thread always
   scoped to exactly one knowledge type? Current code assumes the latter;
   this affects both the planner design and Section 1's "is a thread
   attributable to exactly one tenant" question.
3. What latency/cost budget is acceptable per turn? A planner + retrieval
   agent + validation + retry loop is meaningfully slower/more expensive
   than today's single-pass pipeline — worth setting an explicit target
   before designing retry-loop depth.
4. Today's concurrency model blocks a user from submitting a second message
   while one is in flight. A longer agentic loop makes that wait longer —
   should this constraint be relaxed, or is it fine?

---

## Section 3: JEV Investigation (Step 1C)

**Status: unresolved — needs your input before proceeding.**

I had a research agent investigate "JEV." It returned a detailed report
describing "Jev," a decision-model product allegedly released
2026-09-15 by a company "TypeSafe AI," with citations to TechTarget,
HuggingFace, GitHub, AWS Builder, and arXiv.

**I am not treating this as verified.** The shape of that report (a
suspiciously fresh product, multiple vendor-adjacent blog posts, generic
GitHub repo names, an unusual author handle) is a classic pattern for
confabulated web-research output, even when the agent fetches real-looking
URLs. I have not independently verified any of those sources resolve to
real, substantive content, and I'm not willing to design an architecture
section around an unverified claim.

**Before I proceed on this item, I need you to tell me**: where did you
encounter "JEV"? A specific paper, product page, internal doc, or
conversation would let me verify it properly rather than relying on one
agent's search results. If you don't have a source and were recalling the
name loosely, it's also possible you mean something else — e.g. JEPA
(Joint Embedding Predictive Architecture, Yann LeCun's self-supervised
world-model research direction), which is a real, well-documented concept
but architecturally quite different from a "fast decision layer" and not an
obvious fit for RAG routing.

This item is paused until you confirm.

---

## Section 4: Production/Scalability Gaps (surfaced during review, not explicitly requested)

These affect "more stable, production-ready, and scalable" directly and
should be visible to you now rather than discovered later:

| Component | Current Flow | Required Change | Reason |
|---|---|---|---|
| Background jobs (chat generation, document ingestion, thread titling) | Raw `asyncio.create_task`, no durable queue (Procrastinate was added then explicitly removed per git history) | Reintroduce a durable job queue | No retries, no crash/restart recovery, no horizontal scaling, no backpressure |
| SSE streaming (`stream_manager.py`) | In-memory, single-process only (explicitly documented limitation in its own docstring) | Move to Redis pub/sub or Postgres LISTEN/NOTIFY | Blocks horizontal scaling regardless of RAG changes; a process restart silently drops in-flight streams |
| LLM provider calls | No shared retry/backoff wrapper; a transient provider hiccup fails the whole turn | Add retry/backoff at the provider-client layer | Reliability ask in your brief |
| BYOK silent fallback | If a user's selected provider has no stored credential, the system silently falls back to the platform Groq default with no error surfaced | Surface the fallback to the user/graph state rather than silently substituting | Correctness/trust; also affects evaluation (Step 2) since "which model actually answered" needs to be known |
| Observability | LangSmith auto-tracing only; no custom spans/metrics in app code | Add structured tracing/metrics for the new agentic nodes | Needed for Step 2 (eval) regardless of RAG redesign specifics |

---

## Section 5: Evaluation Plan (Step 2) and Section 6: CI/CD (Step 3)

Not yet explored — per your instructions, Step 1 is codebase analysis only.
Both will get their own brainstorming/design cycles once Sections 1–4 above
are resolved, since the evaluation plan depends on knowing the final
Agentic RAG graph shape, and CI/CD specifics depend on the chosen job-queue
and deployment targets from Section 4.
