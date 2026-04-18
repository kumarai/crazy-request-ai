import { create } from "zustand"
import { persist } from "zustand/middleware"

// Customer identity + active conversation for the support chat. Both are
// persisted to localStorage so a reload restores the same thread. The
// active conversation is scoped per customer — switching customers
// clears it.
interface SupportState {
  customerId: string
  conversationId: string | undefined
  setCustomerId: (id: string) => void
  setConversationId: (id: string | undefined) => void
}

export const useSupportStore = create<SupportState>()(
  persist(
    (set) => ({
      customerId: "",
      conversationId: undefined,
      setCustomerId: (customerId: string) =>
        set({ customerId, conversationId: undefined }),
      setConversationId: (conversationId: string | undefined) =>
        set({ conversationId }),
    }),
    { name: "crazy-ai-support-customer" },
  ),
)
