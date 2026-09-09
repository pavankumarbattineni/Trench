import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios";

export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  withCredentials: true,
});

export interface HealthResponse {
  status: string;
  db: string;
}

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>("/health");
  return data;
}

export interface UserProfile {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
  created_at: string;
}

export async function createSession(
  idToken: string,
  username?: string
): Promise<UserProfile> {
  const { data } = await apiClient.post<UserProfile>("/auth/session", {
    id_token: idToken,
    username,
  });
  return data;
}

export async function getCurrentUser(): Promise<UserProfile> {
  const { data } = await apiClient.get<UserProfile>("/users/me");
  return data;
}

export async function refreshSession(): Promise<UserProfile> {
  const { data } = await apiClient.post<UserProfile>("/auth/refresh");
  return data;
}

export async function logoutSession(): Promise<void> {
  await apiClient.post("/auth/logout");
}

export async function deleteAccountSession(idToken: string): Promise<void> {
  await apiClient.delete("/auth/account", { data: { id_token: idToken } });
}

interface RetriableRequestConfig extends InternalAxiosRequestConfig {
  _retried?: boolean;
}

// Coalesces concurrent 401s into a single /auth/refresh call instead of one per request.
let refreshPromise: Promise<UserProfile> | null = null;

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as RetriableRequestConfig | undefined;
    const isAuthEndpoint = originalRequest?.url?.startsWith("/auth/");

    if (
      error.response?.status === 401 &&
      originalRequest &&
      !originalRequest._retried &&
      !isAuthEndpoint
    ) {
      originalRequest._retried = true;
      try {
        refreshPromise ??= refreshSession().finally(() => {
          refreshPromise = null;
        });
        await refreshPromise;
        return apiClient(originalRequest);
      } catch {
        // Refresh failed too -- fall through and reject with the original 401.
      }
    }

    return Promise.reject(error);
  }
);
