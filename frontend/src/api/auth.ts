// Login / logout / whoami client for the support chat.
//
// All calls use `credentials: "include"` so the signed
// ``support_session`` cookie rides along. Errors surface as thrown
// Error objects so callers can show them in the login dialog.

import type { WhoAmI } from "./types"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"

export async function whoami(): Promise<WhoAmI> {
  const res = await fetch(`${API_BASE}/v1/whoami`, {
    method: "GET",
    credentials: "include",
  })
  if (!res.ok) {
    throw new Error(`whoami failed: ${res.status}`)
  }
  return res.json()
}

export async function login(customer_id: string): Promise<WhoAmI> {
  const res = await fetch(`${API_BASE}/v1/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ customer_id }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`login failed: ${res.status} ${text}`)
  }
  return res.json()
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/v1/logout`, {
    method: "POST",
    credentials: "include",
  })
}
