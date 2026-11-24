import { create } from "zustand";

export interface Membership {
  business_id: string;
  business_name: string;
  role: string;
  permissions: string[];
  all_branches: boolean;
  branch_ids: string[];
}

export interface Me {
  user: { id: string; email: string; full_name: string; phone: string | null; is_owner: boolean; mfa_enabled: boolean; status: string };
  businesses: Membership[];
}

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null; // only when the server is not in cookie mode; memory only
  me: Me | null;
  status: "unknown" | "signed-in" | "signed-out";
  setTokens: (access: string, refresh?: string | null) => void;
  setMe: (me: Me) => void;
  signOut: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  accessToken: null,
  refreshToken: null,
  me: null,
  status: "unknown",
  setTokens: (accessToken, refresh) =>
    set((s) => ({ accessToken, refreshToken: refresh === undefined ? s.refreshToken : refresh })),
  setMe: (me) => set({ me, status: "signed-in" }),
  signOut: () => set({ accessToken: null, refreshToken: null, me: null, status: "signed-out" }),
}));
