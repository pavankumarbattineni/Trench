import { isAxiosError } from "axios";

const FIREBASE_ERROR_MESSAGES: Record<string, string> = {
  "auth/email-already-in-use": "An account with this email already exists.",
  "auth/invalid-email": "That email address looks invalid.",
  "auth/weak-password": "Choose a stronger password (at least 6 characters).",
  "auth/user-not-found": "Incorrect email or password.",
  "auth/wrong-password": "Incorrect email or password.",
  "auth/invalid-credential": "Incorrect email or password.",
  "auth/too-many-requests": "Too many attempts. Please wait a moment and try again.",
  "auth/requires-recent-login": "Please sign in again to confirm it's really you.",
  "auth/invalid-action-code":
    "This reset link is invalid or has expired. Please request a new one.",
  "auth/expired-action-code": "This reset link has expired. Please request a new one.",
  "auth/popup-closed-by-user": "Sign-in was cancelled.",
  "auth/account-exists-with-different-credential":
    "An account already exists for this email using a different sign-in method.",
};

/**
 * Extracts a user-facing message from any error this app can throw:
 * a Firebase Auth error (`.code`), a Trench backend error (axios, with the
 * API's normalized `{status_code, status, message}` body), or anything else.
 */
export function getErrorMessage(error: unknown): string {
  if (isAxiosError(error)) {
    const backendMessage = error.response?.data?.message;
    if (typeof backendMessage === "string") {
      return backendMessage;
    }
  }

  const code = (error as { code?: string } | undefined)?.code;
  if (code && FIREBASE_ERROR_MESSAGES[code]) {
    return FIREBASE_ERROR_MESSAGES[code];
  }

  return "Something went wrong. Please try again.";
}
