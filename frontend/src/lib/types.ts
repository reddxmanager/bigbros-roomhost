// Mirrors backend/app/schema.py. Single source of truth for the Ticket shape is the spec.

export type Dept = 'kitchen' | 'bar' | 'housekeeping' | 'maintenance' | 'frontdesk'
export type Priority = 'urgent' | 'high' | 'normal'
export type Status = 'open' | 'ack' | 'done' | 'cancelled'

export interface Ticket {
  id: string
  created_at: string
  room: string
  guest_session_id: string
  dept: Dept
  summary: string
  item: string | null
  qty: number | null
  priority: Priority
  tags: string[]
  status: Status
  source_utterance: string
}

export interface RoomState {
  room: string
  suite_name: string
  language: string
  standing_tags: string[]
  open_requests: Ticket[]
}

export type WsMessage =
  | { type: 'snapshot'; tickets: Ticket[] }
  | { type: 'ticket.created'; ticket: Ticket }
  | { type: 'ticket.updated'; ticket: Ticket; reason?: string }

export interface TurnResponse {
  reply: string
  tickets: Ticket[]
  language: string
}
