import { create } from "zustand";
import { persist } from "zustand/middleware";

interface BusinessState {
  businessId: string | null;
  setBusiness: (id: string) => void;
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
}

/** The selected business and UI preferences, remembered per browser (never data). */
export const useWorkspace = create<BusinessState>()(
  persist(
    (set) => ({
      businessId: null,
      setBusiness: (businessId) => set({ businessId }),
      sidebarCollapsed: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    }),
    { name: "erp-workspace" },
  ),
);
