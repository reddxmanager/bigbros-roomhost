import { useEffect, useState } from 'react'
import {
  ArrowsClockwise,
  Bell,
  BeerStein,
  Broom,
  ClockCounterClockwise,
  ForkKnife,
  Wrench,
} from '@phosphor-icons/react'
import type { Dept, Ticket, WsMessage } from '../lib/types'
import { openDashboardWs, type WsStatus } from '../lib/ws'
import { fetchHistory, syncGuests } from '../lib/api'
import { playWaitingChime } from '../lib/chime'
import { TicketCard, ageLabel } from './TicketCard'

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

// How long a done card stays on the board before it retires to history. Long
// enough that staff see the state flip land; short enough that a busy week
// never buries the live work under a tail of finished cards.
const DONE_LINGER_MS = 5 * 60_000

// How long a card pulses after a push before settling back. Long enough to
// catch the eye, short enough that the board returns to its calm resting state.
const PUSH_PULSE_MS = 1800

// Department filter. 'all' is the manager view; a single department is the
// wall-tablet view. The URL param wins (so each department bookmarks its own
// board), then the last choice on this device, then everything.
type LaneFilter = 'all' | Dept
const DEPT_LS = 'bigbros.dept'

function initialFilter(): LaneFilter {
  const fromUrl = new URLSearchParams(window.location.search).get('dept')
  const valid = new Set<string>(['all', ...LANES.map((l) => l.key)])
  if (fromUrl && valid.has(fromUrl)) return fromUrl as LaneFilter
  const stored = localStorage.getItem(DEPT_LS)
  if (stored && valid.has(stored)) return stored as LaneFilter
  return 'all'
}

export function Dashboard() {
  const [tickets, setTickets] = useState<Record<string, Ticket>>({})
  // Ids currently pulsing from a fresh push. Transient and view-only: it drives
  // the one-shot pulse + chime, never the ticket data itself.
  const [pushed, setPushed] = useState<Record<string, number>>({})
  const [filter, setFilter] = useState<LaneFilter>(initialFilter)
  // Guest refresh feedback. Empty string means resting.
  const [syncNote, setSyncNote] = useState('')
  const [syncing, setSyncing] = useState(false)
  // Websocket health. The banner only appears when something is wrong; a
  // healthy board shows nothing extra.
  const [wsStatus, setWsStatus] = useState<WsStatus>('connected')
  // Shared minute tick so every card's age chip re-renders together.
  const [now, setNow] = useState(Date.now())
  // Manager's history panel: recently closed tickets with completion times.
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<Ticket[]>([])
  const [historyNote, setHistoryNote] = useState('')

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(id)
  }, [])

  useEffect(() => {
    const pulse = (id: string) => {
      const at = Date.now()
      setPushed((prev) => ({ ...prev, [id]: at }))
      playWaitingChime()
      setTimeout(() => {
        setPushed((prev) => {
          if (prev[id] !== at) return prev // a newer push re-armed it; leave it
          const next = { ...prev }
          delete next[id]
          return next
        })
      }, PUSH_PULSE_MS)
    }

    const removeLater = (id: string, ms: number) => {
      setTimeout(() => {
        setTickets((prev) => {
          const next = { ...prev }
          delete next[id]
          return next
        })
      }, ms)
    }

    const ws = openDashboardWs((m: WsMessage) => {
      // (message handling below; status callback attached as second argument)
      if (m.type === 'snapshot') {
        const next: Record<string, Ticket> = {}
        m.tickets.forEach((t) => {
          // Tickets already cancelled before this tab connected do not get a
          // linger beat. They simply never appear. Done tickets appear but
          // retire on the same clock as live flips, so a reload never
          // resurrects a week of finished work.
          if (t.status === 'cancelled') return
          next[t.id] = t
          if (t.status === 'done') removeLater(t.id, DONE_LINGER_MS)
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
          removeLater(m.ticket.id, CANCEL_LINGER_MS)
        } else {
          setTickets((prev) => ({ ...prev, [m.ticket.id]: m.ticket }))
          // Done cards hold their place briefly, then retire to history.
          if (m.ticket.status === 'done') removeLater(m.ticket.id, DONE_LINGER_MS)
          // A push is the guest (through ATE) or the staleness timer saying the
          // ticket is still outstanding. Pulse the card and sound the cue.
          if (m.reason === 'push') pulse(m.ticket.id)
        }
      }
    }, setWsStatus)
    return () => ws.close()
  }, [])

  function choose(next: LaneFilter) {
    setFilter(next)
    localStorage.setItem(DEPT_LS, next)
  }

  async function refreshGuests() {
    if (syncing) return
    setSyncing(true)
    setSyncNote('')
    try {
      const r = await syncGuests()
      setSyncNote(`Guests updated (${r.updated_rooms.length} suites)`)
    } catch (err) {
      setSyncNote(err instanceof Error ? err.message : 'Guest sync failed')
    } finally {
      setSyncing(false)
      setTimeout(() => setSyncNote(''), 5000)
    }
  }

  async function toggleHistory() {
    const opening = !historyOpen
    setHistoryOpen(opening)
    if (!opening) return
    setHistoryNote('Loading...')
    try {
      setHistory(await fetchHistory())
      setHistoryNote('')
    } catch (err) {
      setHistoryNote(err instanceof Error ? err.message : 'History failed to load')
    }
  }

  // Minutes between two stamps, as "14m" or "2h 05m". Dash when either side
  // is missing (old tickets from before stamps existed, or cancelled work).
  function span(from?: string | null, to?: string | null): string {
    if (!from || !to) return '-'
    const mins = Math.round((new Date(to).getTime() - new Date(from).getTime()) / 60000)
    if (mins < 0) return '-'
    if (mins < 60) return `${mins}m`
    const h = Math.floor(mins / 60)
    return `${h}h ${String(mins % 60).padStart(2, '0')}m`
  }

  const lanes = filter === 'all' ? LANES : LANES.filter((l) => l.key === filter)

  return (
    <div>
      {wsStatus === 'reconnecting' && (
        <div className="ws-banner">
          Connection lost. Reconnecting... the board may be out of date.
        </div>
      )}
      {wsStatus === 'unauthorized' && (
        <div className="ws-banner bad">
          Staff key rejected. Add ?reset=1 to the URL and enter it again.
        </div>
      )}
      <div className="toolbar">
        <label htmlFor="dept-filter" className="toolbar-label">
          Department
        </label>
        <select
          id="dept-filter"
          value={filter}
          onChange={(e) => choose(e.target.value as LaneFilter)}
        >
          <option value="all">All departments</option>
          {LANES.map((l) => (
            <option key={l.key} value={l.key}>
              {l.label}
            </option>
          ))}
        </select>
        <button className="refresh" onClick={refreshGuests} disabled={syncing}>
          <ArrowsClockwise size={14} weight="regular" />
          {syncing ? 'Refreshing...' : 'Refresh guests'}
        </button>
        <button
          className={`refresh${historyOpen ? ' active' : ''}`}
          onClick={toggleHistory}
        >
          <ClockCounterClockwise size={14} weight="regular" />
          History
        </button>
        {syncNote && <span className="sync-note">{syncNote}</span>}
      </div>

      {historyOpen && (
        <div className="history">
          <div className="history-head">
            Recently completed. Open to ack is pickup speed; ack to done is work speed.
          </div>
          {historyNote && <div className="history-note">{historyNote}</div>}
          {!historyNote && history.length === 0 && (
            <div className="history-note">Nothing completed yet.</div>
          )}
          {history.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Closed</th>
                  <th>Dept</th>
                  <th>Room</th>
                  <th>Summary</th>
                  <th>Open to ack</th>
                  <th>Ack to done</th>
                  <th>Total</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {history.map((t) => (
                  <tr key={t.id} className={t.status === 'cancelled' ? 'cancelled' : ''}>
                    <td>
                      {t.done_at
                        ? new Date(t.done_at).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                          })
                        : '-'}
                    </td>
                    <td>{t.dept}</td>
                    <td>{t.room}</td>
                    <td className="hist-summary">{t.summary}</td>
                    <td>{span(t.created_at, t.ack_at)}</td>
                    <td>{span(t.ack_at, t.done_at)}</td>
                    <td>{span(t.created_at, t.done_at)}</td>
                    <td>{t.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      <div className={`board${filter === 'all' ? '' : ' single'}`}>
        {lanes.map((lane) => {
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
                items.map((t) => (
                  <TicketCard key={t.id} t={t} pushed={t.id in pushed} now={now} />
                ))
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
