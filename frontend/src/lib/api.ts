import type { Status } from './types'

// Status changes are server-authoritative. We POST the new status, the backend
// flips it and broadcasts ticket.updated over the websocket, and the card
// re-renders from that broadcast. We deliberately do not mutate local state
// here, so a second dashboard tab reflects the same change.
export async function setTicketStatus(id: string, status: Status): Promise<void> {
  const res = await fetch(`/tickets/${id}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  if (!res.ok) {
    throw new Error(`Status update failed: ${res.status}`)
  }
}
