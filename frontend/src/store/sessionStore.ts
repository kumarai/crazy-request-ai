// Session state — authenticated customer_id + guest flag.
//
// Populated on app boot via `whoami()` so the rest of the UI (support
// chat, cost badge, login modal) can read it synchronously. Updated
// after login/logout and when the backend flips a conversation from
// guest to authed (X-Conversation-Rebound header).

import { create } from "zustand"

export interface SessionState {
  customerId: string | null
  isGuest: boolean
  loaded: boolean
  setSession: (customerId: string, isGuest: boolean) => void
  clear: () => void
}

export const useSessionStore = create<SessionState>((set) => ({
  customerId: null,
  isGuest: true,
  loaded: false,
  setSession: (customerId, isGuest) =>
    set({ customerId, isGuest, loaded: true }),
  clear: () => set({ customerId: null, isGuest: true, loaded: true }),
}))
