import {
  EmailAuthProvider,
  createUserWithEmailAndPassword,
  reauthenticateWithCredential,
  reauthenticateWithPopup,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut as firebaseSignOut,
  updatePassword,
  type User as FirebaseUser,
} from "firebase/auth";

import {
  confirmPasswordResetToken,
  deleteAccountSession,
  getCurrentUser,
  loginWithFirebase,
  logoutRemote,
  logoutSession,
  requestPasswordResetEmail,
  signupOwner,
  type OwnerSignupProfile,
  type UserProfile,
} from "@/lib/api";
import { firebaseAuth, googleProvider } from "@/lib/firebase";

async function establishSession(user: FirebaseUser): Promise<UserProfile> {
  const idToken = await user.getIdToken();
  await loginWithFirebase(idToken);
  return getCurrentUser();
}

/**
 * Registers a new Trench user as the Owner of a brand-new organization --
 * the only way an organization now comes into existence. No session is
 * established here: the backend returns no tokens, so callers must send
 * the user to /signin afterward rather than straight into the app.
 */
export async function signUpOwner(
  username: string,
  email: string,
  password: string,
  organizationName: string
): Promise<OwnerSignupProfile> {
  const credential = await createUserWithEmailAndPassword(firebaseAuth, email, password);
  const idToken = await credential.user.getIdToken();
  return signupOwner(idToken, organizationName, username);
}

/**
 * "Continue with Google" used as a signup action (from the signup page):
 * creates the account AND its organization, same as the email/password
 * path above -- no session is established here either. Google collects
 * no username, so one is auto-generated from the email.
 */
export async function signUpOwnerWithGoogle(
  organizationName: string
): Promise<OwnerSignupProfile> {
  const credential = await signInWithPopup(firebaseAuth, googleProvider);
  const idToken = await credential.user.getIdToken();
  return signupOwner(idToken, organizationName);
}

export async function signIn(email: string, password: string): Promise<UserProfile> {
  const credential = await signInWithEmailAndPassword(firebaseAuth, email, password);
  return establishSession(credential.user);
}

export async function signInWithGoogle(): Promise<UserProfile> {
  const credential = await signInWithPopup(firebaseAuth, googleProvider);
  return establishSession(credential.user);
}

export async function signOutEverywhere(): Promise<void> {
  // Best effort -- a failed network call must never block sign-out, since
  // there's nothing server-side it could have invalidated anyway.
  try {
    await logoutRemote();
  } catch {
    // Intentionally ignored.
  }
  logoutSession();
  await firebaseSignOut(firebaseAuth);
}

/**
 * Fully custom, backend-driven password reset -- the backend generates
 * and validates its own token and updates the password via the Firebase
 * Admin SDK; this no longer goes through Firebase's own
 * sendPasswordResetEmail/oobCode flow at all. Firebase remains the
 * password *store*, not the reset-email/verification owner.
 */
export async function requestPasswordReset(email: string): Promise<void> {
  await requestPasswordResetEmail(email);
}

export async function completePasswordReset(
  token: string,
  newPassword: string,
  confirmPassword: string
): Promise<void> {
  await confirmPasswordResetToken(token, newPassword, confirmPassword);
}

function requireCurrentUser(): FirebaseUser {
  const user = firebaseAuth.currentUser;
  if (!user || !user.email) {
    throw new Error("Please sign in again to continue.");
  }
  return user;
}

async function reauthenticate(user: FirebaseUser, currentPassword: string): Promise<void> {
  const credential = EmailAuthProvider.credential(user.email!, currentPassword);
  await reauthenticateWithCredential(user, credential);
}

export async function changePassword(
  currentPassword: string,
  newPassword: string
): Promise<void> {
  const user = requireCurrentUser();
  await reauthenticate(user, currentPassword);
  await updatePassword(user, newPassword);
}

async function finishAccountDeletion(user: FirebaseUser): Promise<void> {
  const idToken = await user.getIdToken(true);
  await deleteAccountSession(idToken);
  logoutSession();
  await firebaseSignOut(firebaseAuth);
}

export async function deleteAccountWithPassword(currentPassword: string): Promise<void> {
  const user = requireCurrentUser();
  await reauthenticate(user, currentPassword);
  await finishAccountDeletion(user);
}

export async function deleteAccountWithGoogle(): Promise<void> {
  const user = requireCurrentUser();
  await reauthenticateWithPopup(user, googleProvider);
  await finishAccountDeletion(user);
}
