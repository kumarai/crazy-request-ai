import { create } from "zustand"
import { persist } from "zustand/middleware"

// Optional API key. If empty, no X-API-Key header is sent — useful when the
// backend middleware is disabled. Persisted to localStorage.
interface AuthState {
  apiKey: string
  setApiKey: (key: string) => void
  clearApiKey: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      apiKey: "",
      setApiKey: (key: string) => set({ apiKey: key }),
      clearApiKey: () => set({ apiKey: "" }),
    }),
    { name: "crazy-ai-api-key" },
  ),
)
