# Trench Phase 2 — Firebase Authentication: Implementation Notes & Interview Prep

## What was built

Firebase Authentication (Google SSO + email/password) on the frontend,
verified once per session by the backend, which then issues and owns its
own stateless JWT access/refresh tokens in httpOnly cookies. Postgres
gained a `users` table (Firebase UID + email + username + soft-disable +
timestamps). Full lifecycle shipped and verified end-to-end in a real
browser against the real Firebase project and local Postgres: signup,
signin, change password, logout, forgot/reset password, and account
deletion (Postgres row + Firebase identity both removed).

## The complete flow: Frontend → Firebase → Backend → Database → Response

```
Frontend                Firebase Auth           Trench Backend         PostgreSQL
   |                         |                        |                    |
1. signUp/signIn/Google ---> |  (Firebase verifies password,               |
   |                         |   or completes Google OAuth)                |
   | <---- Firebase ID token-|                        |                    |
   |                                                  |                    |
2. POST /auth/session { id_token } -------------->    |                    |
   |                         |                        | 3. verify ID token |
   |                         | <-- Admin SDK verify --|  (local JWT check  |
   |                         |     (signature + exp)  |   vs Google's      |
   |                         |                        |   public keys)     |
   |                         |                        | 4. lazy upsert user|
   |                         |                        |    by firebase_uid |
   |                         |                        |    (1 SELECT, ---->| INSERT if new
   |                         |                        |     +1 INSERT      |
   |                         |                        |     only if new)   |
   |                         |                        | 5. issue Trench's  |
   |                         |                        |    own access_token|
   |                         |                        |    + refresh_token |
   |                         |                        |    (stateless JWTs,|
   |                         |                        |    no DB write)    |
   | <--- Set-Cookie: access_token, refresh_token (httpOnly, lax) ---------|
   |                                                  |                    |
6. GET /users/me (cookie sent automatically) ---->    |                    |
   |                         |                        | 7. verify OUR      |
   |                         |                        |    access_token    |
   |                         |                        |    (stateless)     |
   |                         |                        | 8. SELECT user --->|
   | <----------------- user profile JSON ------------|                    |
```

**Who owns what, concretely:**
- **Firebase alone**: password storage/verification, Google OAuth, the
  identity itself (`firebase_uid`), forgot-password emails, and password
  changes (via client-side reauthentication).
- **Our backend**: verifying the Firebase ID token once per session-start,
  the Postgres `users` row, issuing/verifying Trench's own access and
  refresh tokens, and authorizing every subsequent API call.
- After step 2, the frontend never re-contacts Firebase for a signed-in API
  session — every protected request uses only Trench's own cookies.
  Firebase is only re-contacted for: signup, signin, forgot-password,
  change-password (reauthenticate + update), and signout.

## Why our own access/refresh tokens instead of Firebase's directly

Firebase already has a perfectly good token lifecycle (short-lived ID
token, SDK-managed refresh). We layered our own JWTs on top anyway,
because:
1. **Precedent**: Abyss (the reference project, same author's prior work)
   already does exactly this — exchange the Firebase ID token once for
   app-issued tokens — and the resume's ThinkLoop/TickerLens work depends
   on the same pattern for non-Firebase clients (API keys, OAuth 2.1/PKCE).
2. **Forward compatibility**: Trench will eventually need to authenticate
   clients that aren't the Firebase-aware frontend (an MCP server, for
   instance) — a backend-owned token layer is the seam that makes that
   possible without redesigning auth later.
3. **Decoupling**: the backend's authorization logic (parsing `sub`,
   checking `is_active`) never has to know anything about Firebase's token
   format or Google's public key rotation on every request — only the
   one-time exchange does.

**Both tokens are stateless JWTs — no server-side session table.** This
was an explicit simplification: the frontend is trusted to discard the
refresh token on logout and simply let it expire naturally otherwise. The
real trade-off this creates: there is no way to forcibly revoke a
*specific* refresh token before it expires (no "log out this one stolen
device" button) — the only two levers left are natural expiry
(`refresh_token_expire_days`, 30 days) or deleting the account outright
(which removes the Postgres row entirely, so even a still-valid refresh
token immediately fails at the `SELECT user WHERE id = ...` step). This is
a deliberate scope cut, not an oversight — worth being able to explain
the trade-off explicitly rather than presenting it as free.

## Why access token short-lived + stateless, refresh token longer-lived

The access token (15 min) is verified on *every* request with zero DB
hits — pure JWT signature + expiry check. That's only safe because it's
short-lived: a leaked access token has a small blast radius. The refresh
token (30 days) is used far less often, so even though it's *also*
stateless now, its longer life is the deliberate trade for "don't force
sign-in every 15 minutes." Each token carries a random `jti` claim
specifically so two tokens minted in the same second still differ (this
surfaced as a real test failure during development — two access tokens
issued within the same wall-clock second were byte-identical without it).

## Why httpOnly cookies over localStorage/JS-readable cookies

Abyss's own code uses `js-cookie` (JS-readable) despite its CLAUDE.md
documenting an httpOnly design — a real gap between intent and
implementation there. Trench does what Abyss's docs *say* to do: the
backend sets tokens via `Set-Cookie` with `httponly`, `samesite=lax`, and
`secure` (true outside dev). Frontend JavaScript never touches the raw
token string — no axios interceptor manually attaches an
`Authorization` header; the browser sends the cookie automatically. This
meaningfully narrows the XSS blast radius: a successful script injection
can still make authenticated requests *as* the user (the cookie still gets
sent), but it can't exfiltrate the token itself to read/replay elsewhere.

`SameSite=Lax` works here because `localhost:3000` and `localhost:8000`
are different origins but the *same site* (site = scheme + registrable
domain; port doesn't count). In production across genuinely different
registrable domains, this would need `SameSite=None; Secure` instead —
noted here so it isn't a surprise during deployment.

## Why Google SSO **and** email/password, not just one

The Firebase project was initially configured for Google SSO only; email/
password had to be explicitly enabled in Firebase Console →
Authentication → Sign-in method (a config-only step, no code change).
Both are supported because: Google SSO is the lower-friction default for
most users, while email/password remains necessary for anyone who doesn't
want to link a Google account. The backend doesn't care which provider
was used — `get_or_create_user` keys strictly on `firebase_uid`, which
Firebase issues identically regardless of provider.

**This has one real consequence for the frontend**: not every user has a
Firebase *password* credential. `useFirebaseUser()` (a small hook wrapping
`onAuthStateChanged`) inspects `user.providerData` to check whether
`"password"` is among the linked providers. The Settings page only shows
the "Change password" section for those users, and account deletion
branches: password-linked accounts reauthenticate with
`reauthenticateWithCredential` (password prompt), Google-only accounts
reauthenticate with `reauthenticateWithPopup` (a fresh Google consent,
no password anywhere). Shipping a single "enter your password" deletion
flow for *all* users would have silently broken deletion for every
Google-only account — this was caught during implementation, not assumed
away.

## Why forgot-password and change-password go entirely through Firebase

- **Forgot password**: `sendPasswordResetEmail` — Firebase sends the
  email and returns success *even if the email doesn't exist*. This is
  deliberate on Firebase's part (prevents email enumeration), and the
  frontend's `/forgot-password` page never branches on the outcome — it
  shows the same "check your email" message unconditionally, for the same
  reason.
- **Reset password** (via the emailed link): the link points at Trench's
  own `/reset-password?oobCode=...` (via `actionCodeSettings.url`), not
  Firebase's hosted page. That page calls `verifyPasswordResetCode` (to
  show *which* email is being reset and catch an expired/invalid code up
  front) then `confirmPasswordReset(oobCode, newPassword)`. The backend is
  never involved — Firebase updates the password directly.
- **Change password** (logged in, Settings): `reauthenticateWithCredential`
  (proves the *current* password) then `updatePassword`. Firebase enforces
  "recent login" itself — a stale session throws
  `auth/requires-recent-login`, which the reauthenticate step exists to
  satisfy before the call is even attempted.

Routing any of this through our backend would mean forwarding a raw
password to the Admin SDK for no benefit: the Admin SDK can force-set a
password server-side, but it **cannot verify a current one** — only the
client SDK can, via reauthentication. So there's no way to build a "change
password" backend endpoint that's actually more secure than doing it
client-side; it would just add a hop.

## Why Postgres-then-Firebase deletion order

`AuthService.delete_account`:
1. Verify a **freshly issued** Firebase ID token (`iat` within 5 minutes)
   belongs to the *same* `firebase_uid` as the authenticated session — this
   is the "recent authentication" gate for a destructive, irreversible
   operation, independent of whatever the frontend already did.
2. Delete the Postgres `users` row (single `DELETE`, inside the request's
   transaction).
3. Only then call `firebase_admin.auth.delete_user(uid)`.

If Firebase deletion happens first and step 2 then fails, the user is
locked out permanently with their data still sitting in Postgres and no
way to log back in and ask for its removal — an orphaned-PII failure mode
with no recovery path. Doing Postgres first means the worst case is a
live Firebase account pointing at nothing, which is retriable (the
`/auth/account` call can simply be re-issued — it fails gracefully with
"contact support" if the Firebase step alone didn't succeed).

## Interview questions this phase prepares you for

- "Walk me through what happens between clicking 'Sign in with Google'
  and having an authenticated session." → the full flow diagram above.
- "Why maintain your own JWTs when Firebase already issues tokens?" →
  precedent, forward compatibility for non-Firebase clients, decoupling
  authorization logic from Firebase's token format.
- "What's the security trade-off of stateless refresh tokens?" → no
  targeted revocation before natural expiry; only full account deletion
  or waiting out the 30-day window. Be ready to say when you'd add a
  revocation store back (multi-device "log out everywhere," compromised
  token response).
- "Why httpOnly cookies over localStorage?" → localStorage is readable by
  any script on the page (XSS reads the token directly); httpOnly cookies
  aren't readable by JS at all, though CSRF becomes the thing to reason
  about instead (mitigated here by `SameSite=Lax` plus every mutating
  endpoint requiring the access-token cookie, not just presence of *some*
  cookie).
- "How would you support 'log out of all other devices'?" → not possible
  today without reintroducing server-side refresh token state (a
  `refresh_tokens` table keyed by user, so a rotate/revoke on one row
  doesn't affect others) — explicitly cut from this phase's scope.
- "How does the backend treat two different sign-in providers for the
  same person?" → it doesn't reconcile them — `firebase_uid` is the only
  key; a Google sign-in and an email/password sign-in with the same email
  address are two different Firebase users (and two different Trench
  rows) unless Firebase's own account-linking is used, which this phase
  doesn't implement.
- "Why does account deletion need a *fresh* ID token, not just the
  existing session?" → the access-token cookie alone proves "this browser
  had a valid session sometime in the last 15 minutes," not "the actual
  account owner is driving this specific destructive action right now" —
  the fresh-token check binds the deletion to a real, recent
  authentication event.

## Common mistakes / misconceptions to avoid saying out loud

- Claiming httpOnly cookies make an app "immune to XSS" — they only
  protect the *token itself* from exfiltration; a script can still ride
  along and make authenticated requests.
- Assuming every Firebase user has a password — providers vary
  (`user.providerData`), and code that unconditionally prompts for a
  current password will break for OAuth-only accounts.
- Treating `SameSite=Lax` as equivalent to `SameSite=Strict` — Lax still
  sends cookies on top-level cross-site navigations (which is why this
  works across `localhost:3000`/`:8000`), Strict would not.
- Saying stateless JWTs "can be revoked" — they cannot, by definition,
  before expiry; only a server-side record (which this design explicitly
  omits) enables that.
