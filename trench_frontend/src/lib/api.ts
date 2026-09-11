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

export interface UserOrganization {
  id: string;
  name: string;
  role: string;
}

export interface UserProfile {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
  created_at: string;
  model_id: string | null;
  model_name: string | null;
  organization: UserOrganization | null;
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

export function isAuthenticated(): boolean {
  return Boolean(getAccessToken());
}

export async function loginWithFirebase(
  idToken: string,
  username?: string
): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>("/api/v1/auth/login", {
    id_token: idToken,
    username,
  });
  setAuthTokens(data);
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

export function logoutSession(): void {
  // Stateless JWTs, no server-side session to invalidate -- logout is
  // purely a client-side action (clearing these tokens is all "logout"
  // means to the backend).
  clearAuthTokens();
}

export async function deleteAccountSession(idToken: string): Promise<void> {
  await apiClient.delete("/api/v1/auth/account", { data: { id_token: idToken } });
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
  return url === "/api/v1/auth/login" || url === "/api/v1/auth/refresh";
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
      if (typeof window !== "undefined" && window.location.pathname !== "/signin") {
        window.location.href = "/signin";
      }
    }

    return Promise.reject(error);
  }
);
