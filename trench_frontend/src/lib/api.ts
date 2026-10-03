import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios";
import Cookies from "js-cookie";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const apiClient = axios.create({ baseURL: API_BASE_URL });

export interface HealthResponse {
  status: string;
  db: string;
}

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>("/health");
  return data;
}

export interface UserTenant {
  id: string;
  name: string;
  role: "owner" | "admin" | "member";
}

export interface UserProfile {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
  created_at: string;
  model_id: string | null;
  model_name: string | null;
  // Every user belongs to exactly one tenant under the invitation-only
  // model -- still nullable in the type only for a brief transitional
  // window (see Task 13's NOT NULL migration, not yet applied), never a
  // state new code should expect to handle.
  tenant: UserTenant | null;
  has_company_access: boolean;
}

// Trench uses Bearer-token auth, not cookies for the API itself: the
// backend returns access_token/refresh_token in the response body, and
// this frontend stores them in (non-httpOnly) cookies purely as its own
// browser-side persistence, reading them back to attach
// `Authorization: Bearer <access_token>` on every request. There is no
// server-side session -- possession of a valid, unexpired token is the
// only thing ever checked.
const ACCESS_TOKEN_COOKIE = "a_token";
const REFRESH_TOKEN_COOKIE = "r_token";

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

// Exported (not just used internally) so the SSE stream client
// (chat-stream.ts) can attach the same Bearer token -- `fetch`'s
// ReadableStream-based reader is used instead of EventSource specifically
// because EventSource can't send custom headers, so that client needs
// direct read access to the current token.
export function getAccessToken(): string | undefined {
  return Cookies.get(ACCESS_TOKEN_COOKIE);
}

function getRefreshToken(): string | undefined {
  return Cookies.get(REFRESH_TOKEN_COOKIE);
}

function setAuthTokens(tokens: TokenResponse): void {
  Cookies.set(ACCESS_TOKEN_COOKIE, tokens.access_token, { path: "/" });
  Cookies.set(REFRESH_TOKEN_COOKIE, tokens.refresh_token, { path: "/" });
}

export function clearAuthTokens(): void {
  Cookies.remove(ACCESS_TOKEN_COOKIE, { path: "/" });
  Cookies.remove(REFRESH_TOKEN_COOKIE, { path: "/" });
}

/**
 * Stores a token pair obtained from a flow that lives outside this file
 * (e.g. invitations.ts's acceptInvitation) -- `setAuthTokens` above isn't
 * exported, so this is the seam other modules use to persist a session
 * without duplicating the cookie-writing logic.
 */
export function persistTokens(tokens: TokenResponse): void {
  setAuthTokens(tokens);
}

export function isAuthenticated(): boolean {
  return Boolean(getAccessToken());
}

/**
 * Signs in an *already-registered* user -- never creates one. The backend
 * rejects (404) a valid Firebase ID token that has no matching Trench
 * account; the caller must go through `signupWithFirebase` first. See
 * signup/signin split in AuthService (backend) -- these are deliberately
 * two separate operations, not one lazy "create-if-missing" login.
 */
export async function loginWithFirebase(idToken: string): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>("/api/v1/auth/login", {
    id_token: idToken,
  });
  setAuthTokens(data);
  return data;
}

export interface OwnerSignupTenant {
  id: string;
  name: string;
  domain: string;
}

export interface OwnerSignupProfile {
  tenant: OwnerSignupTenant;
}

/**
 * Registers a new Trench user as the Owner of a brand-new tenant -- the
 * only way an account (and its tenant) now comes into being, other than
 * accepting an invitation (see invitations.ts). No session is established
 * here: the Owner signs in separately via loginWithFirebase afterward,
 * same as every other account-creation path except invite-accept.
 */
export async function signupOwner(
  idToken: string,
  tenantName: string,
  username?: string
): Promise<OwnerSignupProfile> {
  const { data } = await apiClient.post<OwnerSignupProfile>(
    "/api/v1/auth/signup/owner",
    { id_token: idToken, username, tenant_name: tenantName }
  );
  return data;
}

export async function getCurrentUser(): Promise<UserProfile> {
  const { data } = await apiClient.get<UserProfile>("/api/v1/users/me");
  return data;
}

export async function updateSelectedModel(modelId: string): Promise<UserProfile> {
  const { data } = await apiClient.patch<UserProfile>("/api/v1/users/me", {
    model_id: modelId,
  });
  return data;
}

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;
  try {
    // A plain axios call, not the `apiClient` instance -- reusing
    // `apiClient` here would re-enter its own response interceptor below.
    const { data } = await axios.post<TokenResponse>(
      `${API_BASE_URL}/api/v1/auth/refresh`,
      { refresh_token: refreshToken }
    );
    setAuthTokens(data);
    return data.access_token;
  } catch {
    return null;
  }
}

/**
 * Calls the backend's logout endpoint -- stateless JWTs mean there's
 * nothing server-side for it to actually invalidate, but it's still the
 * one authoritative "goodbye" call made before clearing local state. Best
 * effort: a network failure here must never block the user from
 * completing sign-out locally, so callers should swallow errors from this.
 */
export async function logoutRemote(): Promise<void> {
  await apiClient.post("/api/v1/auth/logout");
}

export function logoutSession(): void {
  // Stateless JWTs, no server-side session to invalidate beyond the
  // best-effort logoutRemote() call above -- clearing these tokens is the
  // only thing that actually matters to this browser.
  clearAuthTokens();
}

export async function deleteAccountSession(idToken: string): Promise<void> {
  await apiClient.delete("/api/v1/auth/account", { data: { id_token: idToken } });
}

export async function requestPasswordResetEmail(email: string): Promise<void> {
  await apiClient.post("/api/v1/auth/password-reset/request", { email });
}

export async function confirmPasswordResetToken(
  token: string,
  newPassword: string,
  confirmPassword: string
): Promise<void> {
  await apiClient.post("/api/v1/auth/password-reset/confirm", {
    token,
    new_password: newPassword,
    confirm_password: confirmPassword,
  });
}

// Authenticated Settings > Change Password -- distinct from the Forgot
// Password flow above (requestPasswordResetEmail/confirmPasswordResetToken),
// which is for a signed-out user with no current password to prove.
export async function changePasswordRequest(
  currentPassword: string,
  newPassword: string,
  confirmNewPassword: string
): Promise<void> {
  await apiClient.post("/api/v1/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
    confirm_new_password: confirmNewPassword,
  });
}

// Attaches the stored access token to every outgoing request. Reads the
// cookie fresh each time (no in-memory caching) so it always reflects the
// latest token after a refresh.
apiClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

interface RetriableRequestConfig extends InternalAxiosRequestConfig {
  _retried?: boolean;
}

function isAuthExchange(url: string | undefined): boolean {
  return (
    url === "/api/v1/auth/login" ||
    url === "/api/v1/auth/refresh" ||
    url === "/api/v1/auth/signup/owner"
  );
}

// Mirrors proxy.ts's PROTECTED_PREFIXES -- duplicated rather than imported
// for the same reason proxy.ts documents its own duplication (that file
// runs in the edge runtime; this one needs axios/js-cookie, which don't
// belong there). A dead session on a public page (e.g. /home, which
// already renders correctly for a logged-out visitor) must not force-
// navigate anywhere -- only a route that actually requires auth should.
const PROTECTED_PREFIXES = ["/settings", "/chat", "/documents", "/tenant"];

function isOnProtectedRoute(): boolean {
  if (typeof window === "undefined") return false;
  return PROTECTED_PREFIXES.some((prefix) =>
    window.location.pathname.startsWith(prefix)
  );
}

// Coalesces concurrent 401s into a single refresh call instead of one per request.
let refreshPromise: Promise<string | null> | null = null;

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as RetriableRequestConfig | undefined;

    if (
      error.response?.status === 401 &&
      originalRequest &&
      !originalRequest._retried &&
      !isAuthExchange(originalRequest.url)
    ) {
      originalRequest._retried = true;
      refreshPromise ??= refreshAccessToken().finally(() => {
        refreshPromise = null;
      });
      const newAccessToken = await refreshPromise;

      if (newAccessToken) {
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
        return apiClient(originalRequest);
      }

      clearAuthTokens();
      // Guard against a redirect-reload loop: if we're already sitting on
      // /signin (e.g. a logged-out visitor whose AuthProvider probe still
      // somehow reached here), re-assigning the same URL would just
      // reload the page and re-trigger this exact same 401 -> here again.
      // Only a protected route warrants the hard navigation at all -- a
      // dead session discovered on a public page just means staying
      // logged-out there, which AuthProvider's setUser(null) already
      // handles without any navigation.
      if (
        typeof window !== "undefined" &&
        window.location.pathname !== "/signin" &&
        isOnProtectedRoute()
      ) {
        window.location.href = "/signin";
      }
    }

    return Promise.reject(error);
  }
);
