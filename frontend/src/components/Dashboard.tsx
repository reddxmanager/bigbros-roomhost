import { useEffect, useState } from 'react'
import {
  Bell,
  BeerStein,
  Broom,
  ForkKnife,
  Wrench,
} from '@phosphor-icons/react'
import type { Dept, Ticket, WsMessage } from '../lib/types'
import { openDashboardWs } from '../lib/ws'
import { TicketCard } from './TicketCard'

const LANES: { key: Dept; label: string; icon: typeof ForkKnife }[] = [
  { key: 'kitchen', label: 'Kitchen', icon: ForkKnife },
  { key: 'bar', label: 'Bar', icon: BeerStein },
  { key: 'housekeeping', label: 'Housekeeping', icon: Broom },
  { key: 'maintenance', label: 'Maintenance', icon: Wrench },
  { key: 'frontdesk', label: 'Front Desk', icon: Bell },
]

const PRIORITY_ORDER: Record<string, number> = { urgent: 0, high: 1, normal: 2 }
// Active work sits above resolved work within a lane.
const STATUS_ORDER: Record<string, number> = { open: 0, ack: 0, cancelled: 0, done: 1 }

// How long a cancelled card lingers, struck-through and dimmed, before it is
// removed. The cancel reads as a deliberate action, not a card blinking out.
const CANCEL_LINGER_MS = 2400

export function Dashboard() {
  const [tickets, setTickets] = useState<Record<string, Ticket>>({})

  useEffect(() => {
    const removeLater = (id: string) => {
      setTimeout(() => {
        setTickets((prev) => {
          const next = { ...prev }
          delete next[id]
          return next
        })
      }, CANCEL_LINGER_MS)
    }

    const ws = openDashboardWs((m: WsMessage) => {
      if (m.type === 'snapshot') {
        const next: Record<string, Ticket> = {}
        m.tickets.forEach((t) => {
          // Tickets already cancelled before this tab connected do not get a
          // linger beat. They simply never appear.
          if (t.status !== 'cancelled') next[t.id] = t
        })
        setTickets(next)
      } else if (m.type === 'ticket.created') {
        setTickets((prev) => ({ ...prev, [m.ticket.id]: m.ticket }))
      } else if (m.type === 'ticket.updated') {
        if (m.ticket.status === 'cancelled') {
          setTickets((prev) => {
            // Only show the linger beat if we were actually displaying it.
            if (!prev[m.ticket.id]) return prev
            return { ...prev, [m.ticket.id]: m.ticket }
          })
          removeLater(m.ticket.id)
        } else {
          setTickets((prev) => ({ ...prev, [m.ticket.id]: m.ticket }))
        }
      }
    })
    return () => ws.close()
  }, [])

  return (
    <div className="board">
      {LANES.map((lane) => {
        const LaneIcon = lane.icon
        const items = Object.values(tickets)
          .filter((t) => t.dept === lane.key)
          .sort((a, b) => {
            const s = (STATUS_ORDER[a.status] ?? 0) - (STATUS_ORDER[b.status] ?? 0)
            if (s !== 0) return s
            return (PRIORITY_ORDER[a.priority] ?? 9) - (PRIORITY_ORDER[b.priority] ?? 9)
          })
        return (
          <div key={lane.key} className="lane">
            <div className="lane-head">
              <LaneIcon size={18} weight="regular" />
              <span className="title">{lane.label}</span>
              <span className="count">{items.length}</span>
            </div>
            {items.length === 0 ? (
              <div className="lane-empty">No open tickets.</div>
            ) : (
              items.map((t) => <TicketCard key={t.id} t={t} />)
            )}
          </div>
        )
      })}
    </div>
  )
}
