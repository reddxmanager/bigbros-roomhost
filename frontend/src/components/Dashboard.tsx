import { useEffect, useState } from 'react'
import type { Dept, Ticket, WsMessage } from '../lib/types'
import { openDashboardWs } from '../lib/ws'
import { TicketCard } from './TicketCard'

const LANES: { key: Dept; label: string }[] = [
  { key: 'kitchen', label: 'Kitchen' },
  { key: 'bar', label: 'Bar' },
  { key: 'housekeeping', label: 'Housekeeping' },
  { key: 'maintenance', label: 'Maintenance' },
  { key: 'frontdesk', label: 'Front Desk Triage' },
]

const PRIORITY_ORDER: Record<string, number> = { urgent: 0, high: 1, normal: 2 }

export function Dashboard() {
  const [tickets, setTickets] = useState<Record<string, Ticket>>({})

  useEffect(() => {
    const ws = openDashboardWs((m: WsMessage) => {
      if (m.type === 'snapshot') {
        const next: Record<string, Ticket> = {}
        m.tickets.forEach((t) => {
          next[t.id] = t
        })
        setTickets(next)
      } else if (m.type === 'ticket.created' || m.type === 'ticket.updated') {
        setTickets((prev) => ({ ...prev, [m.ticket.id]: m.ticket }))
      }
    })
    return () => ws.close()
  }, [])

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(5, 1fr)',
        gap: 12,
        padding: 16,
      }}
    >
      {LANES.map((lane) => {
        const items = Object.values(tickets)
          .filter((t) => t.dept === lane.key)
          .sort(
            (a, b) =>
              (PRIORITY_ORDER[a.priority] ?? 9) - (PRIORITY_ORDER[b.priority] ?? 9),
          )
        return (
          <div
            key={lane.key}
            style={{ background: '#f4f4f4', padding: 8, minHeight: 200 }}
          >
            <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>
              {lane.label} ({items.length})
            </h3>
            {items.map((t) => (
              <TicketCard key={t.id} t={t} />
            ))}
          </div>
        )
      })}
    </div>
  )
}
