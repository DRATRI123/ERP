import { create } from "zustand";
import { persist } from "zustand/middleware";

interface BusinessState {
  businessId: string | null;
  setBusiness: (id: string) => void;
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  /** Sidebar groups folded by the user (e.g. "manage"). */
  foldedGroups: string[];
  toggleGroup: (g: string) => void;
  /** Screen language: English, Hindi or Gujarati. */
  lang: "en" | "hi" | "gu";
  setLang: (l: "en" | "hi" | "gu") => void;
}

/** The selected business and UI preferences, remembered per browser (never data). */
export const useWorkspace = create<BusinessState>()(
  persist(
    (set) => ({
      businessId: null,
      setBusiness: (businessId) => set({ businessId }),
      sidebarCollapsed: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      foldedGroups: [],
      lang: "en",
      setLang: (lang) => set({ lang }),
      toggleGroup: (g) => set((s) => ({ foldedGroups: s.foldedGroups.includes(g) ? s.foldedGroups.filter((x) => x !== g) : [...s.foldedGroups, g] })),
    }),
    { name: "erp-workspace" },
  ),
);
