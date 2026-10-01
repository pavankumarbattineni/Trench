# Multi-Tenant Invitation-Based RBAC — Design Spec

Date: 2026-10-01
Status: Approved by user 2026-10-01 — ready for implementation planning
Precedes: Agentic RAG redesign (1B), JEV integration (1C), Evaluation (Step 2),
CI/CD (Step 3) — those are separate specs, sequenced after this one per
`docs/superpowers/architecture-overhaul-step1-tracker.md`.

## Purpose

Replace Trench's current loose, self-service, partially-enforced
organization model with a strict, invitation-only, multi-tenant RBAC system.
Every account belongs to exactly one organization; organizations can never
see each other's data; roles are assigned by the organization, never chosen
by the user.

Full current-state analysis and the decision trail behind every choice below
live in `docs/superpowers/architecture-overhaul-step1-tracker.md` (Section
1). This spec is the actionable distillation of that analysis — read the
tracker for *why*, this spec for *what to build*.

## Explicit quality bar for this implementation

The user has stated they can review backend correctness themselves but
cannot reliably catch frontend issues. **Frontend code must receive the same
rigor as backend code**: no redundant state, no prop-drilling where context/
hooks already exist, consistent with the existing `react-query` + `zod` +
`react-hook-form` patterns already in the codebase, no premature
abstraction, and must be manually exercised in a browser (not just
type-checked) before being called done, per this project's standing
verification requirements. Both frontend and backend should come out
production-ready: no commented-out code, no TODOs left in, no console.log
debugging left behind, proper error states (not just happy-path UI), loading
states for every async action, and accessible form semantics (labels,
aria-invalid on validation errors) on new forms.

## Out of scope (explicitly deferred)

- Multi-organization membership per user (permanent 1-org-per-user).
- DNS TXT-based domain verification (deferred to a future phase; current
  email-derived-domain + public-provider-blocklist level is kept).
- Cross-org knowledge-base sharing.
- Postgres Row-Level Security (application/service-layer enforcement is the
  bar for this phase; revisit as a separate hardening project later).
- Per-tenant custom domains/subdomains for routing.

---

## 1. Data model changes

### 1.1 `organization_members.role` — extend enum

Current: `"admin" | "member"`. New: `"owner" | "admin" | "member"`.

`Organization.owner_user_id` remains the canonical pointer to the Owner, but
the Owner **also** gets an `OrganizationMember` row with `role="owner"` so
all role checks can go through one column rather than two different sources
of truth. A DB-level invariant (check constraint or enforced in the service
layer with a test covering it): there is exactly one `role="owner"` member
per organization, and it is never removed — only transferable in a future
phase (not this one; no "transfer ownership" UI is being built now).

### 1.2 New table: `invitations`

```
invitations
  id (uuid, pk)
  organization_id (fk -> organizations, required)
  email (string, required)
  role (enum: "admin" | "member", required — never "owner")
  token_hash (string, required, unique) -- sha256 of the raw token; raw
                                          -- token is never stored
  status (enum: "pending" | "accepted" | "revoked" | "expired", default pending)
  invited_by_user_id (fk -> users, required)
  expires_at (timestamptz, required) -- created_at + 7 days
  accepted_at (timestamptz, nullable)
  created_at (timestamptz, default now)
```

Index: `(organization_id, email)` — used to detect/block a duplicate pending
invite to the same email within the same org, and to drive the "resend"
action (re-use the row, rotate `token_hash`/`expires_at`, keep `status=pending`).

The raw token is a cryptographically random value (e.g. 32 bytes,
URL-safe base64), emailed once, never persisted in plaintext, and never
logged. Only its hash is stored, following the same "secrets are never
returned/logged in plaintext" convention already used for BYOK credentials.

### 1.3 New table: `organization_credentials`

Mirrors `user_credentials`, but scoped to an org and settable only by the
Owner:

```
organization_credentials
  id (uuid, pk)
  organization_id (fk -> organizations, required, unique with provider_type)
  provider_type (enum, same values as user_credentials.provider_type)
  encrypted_credential (string, required)
  set_by_user_id (fk -> users, required) -- must be the Owner; enforced in
                                           -- the service layer, not the DB
  validated_at (timestamptz, nullable)
  created_at (timestamptz, default now)
  UniqueConstraint(organization_id, provider_type)
```

Encrypted the same way as `user_credentials` (Fernet, `purpose="byok"`
derivation) — no new encryption mechanism introduced.

### 1.4 New table: `password_reset_tokens`

Replaces reliance on Firebase's own `oobCode` reset flow:

```
password_reset_tokens
  id (uuid, pk)
  user_id (fk -> users, required)
  token_hash (string, required, unique)
  expires_at (timestamptz, required) -- created_at + 1 hour
  used_at (timestamptz, nullable)
  created_at (timestamptz, default now)
```

Same "store only the hash" pattern as invitations. A token is single-use:
`used_at` is set atomically when consumed, and a second attempt with the
same token is rejected even before checking expiry.

### 1.5 `users` table

Add `organization_id` (fk -> organizations, nullable during migration,
**NOT NULL after the data-wipe migration completes** — see Section 5). This
replaces "derive tenant via `OrganizationMember` join" as the primary lookup
path; the `OrganizationMember` row remains the source of truth for role, but
every request's tenant-scoping check can use `users.organization_id`
directly without a join.

### 1.6 Migration ordering

1. Add `invitations`, `organization_credentials`, `password_reset_tokens`
   tables (additive, no risk).
2. Add `organization_members.role = 'owner'` enum value; add
   `users.organization_id` as **nullable** first.
3. Run the data wipe (Section 5) — after this, the table is empty, so
   flipping `users.organization_id` to **NOT NULL** in a follow-up migration
   is a zero-risk schema change (no existing rows to violate it).
4. Remove the old self-service org-creation path's now-dead code
   (`create-organization-form.tsx`'s auto-add-same-domain-users behavior in
   `OrganizationService.create`).

---

## 2. Auth & onboarding flows

### 2.1 Owner signup (replaces today's open signup)

- Firebase account creation stays (email+password or Google), but
  **immediately followed by**, in one backend transaction: create
  `Organization` (domain derived from the verified email, rejected if the
  domain is on the public-provider blocklist — same check as today, just
  moved earlier/made mandatory rather than optional), create `User`, create
  `OrganizationMember(role="owner")`.
- There is no more "sign up with any email, optionally create an org later."
  Signup *is* org creation. The frontend signup form gains an "Organization
  name" field; the "Register as administrator" toggle (the old Trench
  app-admin bootstrap concept) is removed — Trench app-admin and org-admin
  were already conceptually distinct, and the new OWNER role supersedes the
  need for a separate global-admin signup path for this flow. (If a
  separate Trench *platform* admin concept is still needed for
  operator-side tooling, that's a different, unrelated concern — not
  addressed by this spec, and not currently blocking anything in it.)

### 2.2 Inviting employees

Two entry points, same backend logic underneath:

- **Individual add** (Organization page): Owner/Admin enters an email +
  picks a role. Admins are restricted to `role="member"` at the API layer
  (not just hidden in the UI — a direct API call attempting
  Admin-assigns-Admin must be rejected with 403).
- **Bulk upload** (Organization page, new): CSV/Excel upload with required
  columns `email, role`. Parsed server-side (not trusted client-side
  parsing for a security-relevant operation), validated row-by-row:
  - malformed email → row rejected, included in a per-row error report
    returned to the uploader (not a single all-or-nothing failure)
  - role outside the uploader's permission (e.g. Admin row in a file
    uploaded by an Admin) → row rejected, reported
  - duplicate email already a member or already has a pending invite →
    row rejected, reported (resend is a separate explicit action, not
    implicit via re-upload)
  - valid rows → one `Invitation` row each, one email each

Every successful `Invitation` triggers an SMTP email with the accept link
(`https://app/invite/accept?token=...`). Expiry: 7 days. Owner/Admin can
resend (rotates token+expiry on the same row) or revoke a pending invite
from the Organization page's members/invites list.

### 2.3 Invite acceptance

- Accept link opens a dedicated page that looks up the invitation by the
  token's hash (reject if missing/expired/revoked/already-accepted).
- User authenticates (Firebase email/password signup, or Google) — **the
  authenticated email must exactly match `invitation.email`**; on mismatch,
  reject with a clear message ("this invite was sent to X, please sign in
  with that address") rather than silently proceeding or silently
  switching the invite's target email.
- On match: create `User` (if this is their first Trench signup) or attach
  the existing Firebase-linked identity, create `OrganizationMember` with
  the invitation's `role`, set `users.organization_id`, mark invitation
  `accepted`. No role is ever chosen by the invitee at this step.

### 2.4 Password reset (fully custom, replaces Firebase's reset flow)

- `POST /auth/password-reset/request {email}` — always returns the same
  "check your email" response regardless of whether the account exists
  (preserve today's anti-enumeration UX). If it exists, create a
  `PasswordResetToken`, email the raw token as a link via SMTP.
- `POST /auth/password-reset/confirm {token, new_password}` — validate
  token (exists, unused, unexpired), update the password via the Firebase
  Admin SDK (`update_user(uid, password=...)`), mark the token used.
  Frontend `forgot-password/page.tsx` and `reset-password/page.tsx` +
  `reset-password-form.tsx` call these new endpoints instead of
  `firebaseAuth`'s client reset functions directly.

---

## 3. Authorization / permission model

### 3.1 Role matrix

| Action | OWNER | ADMIN | MEMBER |
|---|---|---|---|
| Invite/add employee (role=member) | Yes | Yes | No |
| Invite/add employee (role=admin) | Yes | No | No |
| Bulk upload employees | Yes | Yes (member-role rows only) | No |
| Remove a Member | Yes | Yes | No |
| Remove an Admin | Yes | No | No |
| Remove the Owner | No (disallowed entirely) | No | No |
| Promote Member → Admin | Yes | No | No |
| Demote Admin → Member | Yes | No | No |
| Upload org documents / manage org KB | Yes | Yes | No |
| Grant/revoke a Member's KB access | Yes | Yes | No |
| Query org KB (if not revoked) | Yes | Yes | Yes |
| Query own personal KB | Yes | Yes | Yes |
| Set org-wide BYOK credential | Yes | No | No |
| Manage own personal BYOK credential | Yes | Yes | Yes |

### 3.2 Enforcement

- A new `get_current_tenant` dependency resolves `(user, organization_id,
  role)` once per request from the JWT + `users.organization_id`, and is
  applied at **router level** (`APIRouter(dependencies=[...])`) for
  `/documents`, `/threads`, `/credentials`, `/users`, `/organizations` —
  not opted into per-endpoint, closing the gap the Step 1 review found.
- A small set of role-gate dependencies (`require_owner`, `require_admin_or_owner`)
  build on top of it for the specific mutating endpoints in the matrix
  above. Every one of them is unit-tested against all three roles (positive
  and negative cases), not just the happy path.

---

## 4. BYOK credential resolution

Resolution order, computed at the point `LLMClientService`/vector-store
access needs a client:

- **Personal KB query**: use the querying user's own `UserCredential` for
  the needed `provider_type` if present and valid; else platform default
  (Groq). Unchanged from today.
- **Org KB query**: use `organization_credentials` for the needed
  `provider_type` if the Owner has set one; else platform default (Groq).
  **Never** falls back to a non-Owner member's personal credential, even if
  one exists — a Member/Admin's own key only ever powers their personal KB.
- The resolved source (platform default vs. user BYOK vs. org BYOK) is
  attached to the graph state so it's visible for debugging/future
  evaluation (addresses a gap the Step 1 review flagged: today's silent
  Groq fallback is invisible to the caller).

---

## 5. Data migration (destructive — confirmed by user 2026-10-01)

Before the new system activates: delete all existing `User` rows from
Postgres (cascading to their `OrganizationMember`, `Document`, `Thread`,
`ChatHistory`, `UserCredential` rows per existing FK cascade behavior — to
be verified during implementation, not assumed) and delete all corresponding
Firebase Auth users via the Admin SDK. This runs once, as an explicit
migration script, **re-confirmed with the user immediately before
execution** (not auto-run as part of a normal migration pipeline) given its
irreversibility. After this, `users.organization_id NOT NULL` is applied as
described in Section 1.6.

---

## 6. Frontend changes

Given the user's explicit ask for extra frontend rigor, each change below
should be implemented with proper loading/error/empty states and exercised
in a running browser before being considered done, not just type-checked.

- **Remove**: `create-organization-form.tsx` and the "not in org, are you
  an admin?" dead-end state in `organization/page.tsx` (§3 of the frontend
  review) — every authenticated user now always has an org.
- **Signup page**: add organization-name field; remove the admin-bootstrap
  toggle and `getAdminStatus` dependency.
- **New: Invite-accept page** (`/invite/accept`): token lookup, sign-in/sign-up
  form, exact-email-match enforcement with a clear mismatch error state,
  expired/revoked/already-accepted states each with distinct messaging
  (not one generic "invalid invite" catch-all).
- **Organization page**: add/extend
  - individual "invite employee" form (email + role picker, role options
    constrained client-side to match the current user's permission — and
    re-validated server-side regardless)
  - bulk CSV/Excel upload UI: file picker, a downloadable template
    (`email,role` header row), a per-row success/error report after
    submission (not a single pass/fail toast)
  - pending-invites list with resend/revoke actions
  - member list updated for the three-role model (today's UI assumes two)
- **Settings/providers page**: Owner sees an additional "Organization API
  key" section (separate from their personal BYOK section) with the same
  save/validate/delete pattern as personal credentials; Admin/Member do not
  see this section at all (not just disabled — absent, since they have no
  permission to act on it).
- **Forgot/reset password pages**: switch to the new custom backend
  endpoints; preserve the existing anti-enumeration UX on the request page.
- **`auth-provider.tsx` / `UserProfile` type**: `organization` becomes
  non-nullable (every user has exactly one); `role` gains the `"owner"`
  value wherever it's currently typed as `"admin" | "member"`.

---

## 7. Security notes

- Invitation and password-reset tokens: random, hashed at rest, single-use
  where applicable (reset tokens), time-limited, never logged in plaintext
  — consistent with existing BYOK-credential handling conventions in this
  codebase.
- Exact-email-match on invite accept is the primary defense against the
  domain restriction being bypassed by accepting an invite with an
  unrelated account.
- Bulk upload is parsed and validated entirely server-side; the file itself
  is not persisted beyond the request (no need to retain uploaded
  CSV/Excel files once rows are processed into `Invitation` rows).
- SMTP credentials (host/port/user/password) are new config, added to
  `TRENCH_CONFIG` the same way `FIREBASE`/`GROQ`/`PINECONE` config sections
  already exist — never hardcoded, never logged.

## 8. Testing expectations

- Backend: unit tests for the role matrix (every action × every role,
  positive and negative), invitation lifecycle (create/resend/revoke/expire/
  accept, including the exact-email-match rejection case), password-reset
  token lifecycle (valid/expired/reused), BYOK resolution order (personal
  vs. org vs. platform default, including the "never falls back to another
  member's personal key" case).
- Frontend: exercised manually in a browser for every new/changed flow
  (signup-as-owner, invite individual, bulk upload with a mixed-valid/invalid
  file, invite accept with matching and mismatched email, password reset
  end-to-end, role-gated UI visibility for each of the three roles) before
  being reported as done.

## 9. Defaults assumed (flag if wrong, otherwise proceeding as stated)

- Invitation expiry: 7 days.
- Password-reset token expiry: 1 hour.
- SMTP credentials: to be provided as config/env during implementation (no
  specific provider mandated — "use SMTP" was the explicit instruction).
