import type { Status, Ticket } from './types'
import { apiUrl, staffHeaders } from './config'

// Status changes are server-authoritative. We POST the new status, the backend
// flips it and broadcasts ticket.updated over the websocket, and the card
// re-renders from that broadcast. We deliberately do not mutate local state
// here, so a second dashboard tab reflects the same change.
export async function setTicketStatus(id: string, status: Status): Promise<void> {
  const res = await fetch(apiUrl(`/tickets/${id}/status`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...staffHeaders() },
    body: JSON.stringify({ status }),
  })
  if (!res.ok) {
    throw new Error(`Status update failed: ${res.status}`)
  }
}

// Manual guest refresh for the check-in moment: pulls current guests from
// KUYA into room state. The timer does the same on its own; this button is
// for "they just walked in."
// Recently closed tickets for the manager's history view, newest first.
export async function fetchHistory(limit = 50): Promise<Ticket[]> {
  const res = await fetch(apiUrl(`/tickets/history?limit=${limit}`), {
    headers: staffHeaders(),
  })
  if (!res.ok) {
    throw new Error(`History fetch failed: ${res.status}`)
  }
  return res.json()
}

export async function syncGuests(): Promise<{ ok: boolean; updated_rooms: string[] }> {
  const res = await fetch(apiUrl('/sync/guests'), {
    method: 'POST',
    headers: staffHeaders(),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `Guest sync failed: ${res.status}`)
  }
  return res.json()
}
