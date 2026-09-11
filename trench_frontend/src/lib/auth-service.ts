import {
  EmailAuthProvider,
  confirmPasswordReset,
  createUserWithEmailAndPassword,
  reauthenticateWithCredential,
  reauthenticateWithPopup,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut as firebaseSignOut,
  updatePassword,
  verifyPasswordResetCode,
  type User as FirebaseUser,
} from "firebase/auth";

import {
  deleteAccountSession,
  getCurrentUser,
  loginWithFirebase,
  logoutSession,
  signupWithFirebase,
  type SignupProfile,
  type UserProfile,
} from "@/lib/api";
import { firebaseAuth, googleProvider } from "@/lib/firebase";

async function establishSession(user: FirebaseUser): Promise<UserProfile> {
  const idToken = await user.getIdToken();
  await loginWithFirebase(idToken);
  return getCurrentUser();
}

/**
 * Registers a new Trench user. Deliberately does NOT sign the user in --
 * signup and signin are separate actions now, so this never sets any
 * session tokens. Callers should send the user to /signin next, not to
 * the authenticated app.
 */
export async function signUp(
  username: string,
  email: string,
  password: string,
  registerAsAdmin?: boolean
): Promise<SignupProfile> {
  const credential = await createUserWithEmailAndPassword(firebaseAuth, email, password);
  const idToken = await credential.user.getIdToken();
  return signupWithFirebase(idToken, username, registerAsAdmin);
}

/**
 * "Continue with Google" used as a signup action (from the signup page):
 * creates the account (if new) but does NOT log the user in -- mirrors
 * the email/password split above. Google collects no username, so one is
 * auto-generated from the email.
 */
export async function signUpWithGoogle(registerAsAdmin?: boolean): Promise<SignupProfile> {
  const credential = await signInWithPopup(firebaseAuth, googleProvider);
  const idToken = await credential.user.getIdToken();
  return signupWithFirebase(idToken, undefined, registerAsAdmin);
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
  logoutSession();
  await firebaseSignOut(firebaseAuth);
}

export async function requestPasswordReset(email: string): Promise<void> {
  await sendPasswordResetEmail(firebaseAuth, email, {
    url: `${window.location.origin}/reset-password`,
  });
}

export async function verifyResetCode(oobCode: string): Promise<string> {
  return verifyPasswordResetCode(firebaseAuth, oobCode);
}

export async function completePasswordReset(
  oobCode: string,
  newPassword: string
): Promise<void> {
  await confirmPasswordReset(firebaseAuth, oobCode, newPassword);
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
