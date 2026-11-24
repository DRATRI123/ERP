import { api, refreshSession } from "@/lib/api";
import { useAuth, type Me } from "@/stores/auth";
import { useWorkspace } from "@/stores/business";

export async function loadMe(): Promise<Me> {
  const me = await api<Me>("/auth/me");
  useAuth.getState().setMe(me);
  const ws = useWorkspace.getState();
  if (!me.businesses.some((b) => b.business_id === ws.businessId) && me.businesses[0]) {
    ws.setBusiness(me.businesses[0].business_id);
  }
  return me;
}

/** On page load: restore the session from the refresh cookie, if any. */
export async function restoreSession(): Promise<boolean> {
  if (!(await refreshSession())) {
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
    await api("/auth/logout", { method: "POST" });
  } catch {
    /* already signed out */
  }
  useAuth.getState().signOut();
}
