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
  createSession,
  deleteAccountSession,
  logoutSession,
  type UserProfile,
} from "@/lib/api";
import { firebaseAuth, googleProvider } from "@/lib/firebase";

async function establishSession(user: FirebaseUser, username?: string): Promise<UserProfile> {
  const idToken = await user.getIdToken();
  return createSession(idToken, username);
}

export async function signUp(
  username: string,
  email: string,
  password: string
): Promise<UserProfile> {
  const credential = await createUserWithEmailAndPassword(firebaseAuth, email, password);
  return establishSession(credential.user, username);
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
  await logoutSession();
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
