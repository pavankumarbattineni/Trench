import { apiClient } from "@/lib/api";
import type { TokenResponse } from "@/lib/api";

/**
 * Accepts a tenant invitation. The invited email comes from
 * Firebase authentication (the `idToken`), not from any form field the
 * person could mistype -- the backend rejects a mismatch between the
 * authenticated email and the invitation's target email.
 */
export async function acceptInvitation(
  token: string,
  idToken: string,
  username?: string
): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>(
    "/api/v1/auth/invitations/accept",
    { token, id_token: idToken, username }
  );
  return data;
}
