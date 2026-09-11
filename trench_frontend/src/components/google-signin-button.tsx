"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { signInWithGoogle, signUpWithGoogle } from "@/lib/auth-service";
import { getErrorMessage } from "@/lib/errors";
import type { SignupProfile, UserProfile } from "@/lib/api";

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M23.49 12.27c0-.79-.07-1.54-.2-2.27H12v4.3h6.47a5.53 5.53 0 0 1-2.4 3.63v3h3.88c2.27-2.09 3.54-5.17 3.54-8.66z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.24 0 5.95-1.07 7.93-2.91l-3.88-3a7.4 7.4 0 0 1-11-3.9H1.05v3.09A12 12 0 0 0 12 24z"
      />
      <path
        fill="#FBBC05"
        d="M5.05 14.19a7.2 7.2 0 0 1 0-4.38V6.72H1.05a12 12 0 0 0 0 10.56l4-3.09z"
      />
      <path
        fill="#EA4335"
        d="M12 4.75c1.76 0 3.34.6 4.59 1.79l3.44-3.44C17.94 1.19 15.24 0 12 0 7.31 0 3.26 2.69 1.05 6.72l4 3.09A7.17 7.17 0 0 1 12 4.75z"
      />
    </svg>
  );
}

type GoogleSignInButtonProps =
  | {
      /** Signs in an existing account -- rejects (with a clear error) an
       * email that hasn't signed up yet. */
      mode: "signin";
      onSuccess: (profile: UserProfile) => void;
      onError: (message: string) => void;
    }
  | {
      /** Creates the account (if new) but does NOT log the user in --
       * mirrors the email/password signup form's behavior. */
      mode: "signup";
      registerAsAdmin?: boolean;
      onSuccess: (profile: SignupProfile) => void;
      onError: (message: string) => void;
    };

export function GoogleSignInButton(props: GoogleSignInButtonProps) {
  const [loading, setLoading] = useState(false);

  const handleClick = async () => {
    setLoading(true);
    try {
      if (props.mode === "signup") {
        props.onSuccess(await signUpWithGoogle(props.registerAsAdmin));
      } else {
        props.onSuccess(await signInWithGoogle());
      }
    } catch (error) {
      props.onError(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Button
      type="button"
      variant="outline"
      className="w-full gap-2"
      disabled={loading}
      onClick={handleClick}
    >
      <GoogleIcon />
      {loading ? "Connecting…" : "Continue with Google"}
    </Button>
  );
}
