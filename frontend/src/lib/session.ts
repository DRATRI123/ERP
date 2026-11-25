import { api, refreshSession } from "@/lib/api";
import { isOnline, rememberedMe, rememberMe } from "@/lib/offline";
import { useAuth, type Me } from "@/stores/auth";
import { useWorkspace } from "@/stores/business";

export async function loadMe(): Promise<Me> {
  const me = await api<Me>("/auth/me");
  useAuth.getState().setMe(me);
  rememberMe(me);
  const ws = useWorkspace.getState();
  if (!me.businesses.some((b) => b.business_id === ws.businessId) && me.businesses[0]) {
    ws.setBusiness(me.businesses[0].business_id);
  }
  return me;
}

/** On page load: restore the session from the refresh cookie, if any. */
export async function restoreSession(): Promise<boolean> {
  // No internet: carry on with the last signed-in profile so billing still works; the session
  // cookie signs in again on the first request once the connection is back.
  const cached = rememberedMe();
  if (!isOnline() && cached) {
    useAuth.getState().setMe(cached);
    return true;
  }
  if (!(await refreshSession())) {
    if (!isOnline() && cached) {
      useAuth.getState().setMe(cached);
      return true;
    }
    useAuth.getState().signOut();
    return false;
  }
  try {
    await loadMe();
    return true;
  } catch {
    useAuth.getState().signOut();
    return false;
  }
}

export async function signOut() {
  try {
    localStorage.removeItem("erp-offline-me"); // unsynced bills stay queued for the next sign-in
  } catch {
    /* storage blocked */
  }
  try {
    await api("/auth/logout", { method: "POST" });
  } catch {
    /* already signed out */
  }
  useAuth.getState().signOut();
}
