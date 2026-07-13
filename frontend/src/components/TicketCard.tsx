import { useState } from 'react'
import {
  ArrowUp,
  ArrowsClockwise,
  Bell,
  BeerStein,
  Broom,
  CaretDown,
  CheckCircle,
  ForkKnife,
  HandWaving,
  Warning,
  WarningOctagon,
  Wrench,
} from '@phosphor-icons/react'
import type { Dept, Status, Ticket } from '../lib/types'
import { setTicketStatus } from '../lib/api'

const DEPT_ICON: Record<Dept, typeof ForkKnife> = {
  kitchen: ForkKnife,
  bar: BeerStein,
  housekeeping: Broom,
  maintenance: Wrench,
  frontdesk: Bell,
}

// open -> ack -> done. done is terminal in this control.
const NEXT_STATUS: Partial<Record<Status, { to: Status; label: string }>> = {
  open: { to: 'ack', label: 'Acknowledge' },
  ack: { to: 'done', label: 'Mark done' },
}

function classifyTag(tag: string): 'allergy' | 'repeat' | 'escalation' | 'plain' {
  if (tag.startsWith('allergy:')) return 'allergy'
  if (tag === 'repeat') return 'repeat'
  if (tag === 'escalation') return 'escalation'
  return 'plain'
}

function tagLabel(tag: string): string {
  if (tag.startsWith('allergy:')) return `allergy: ${tag.slice('allergy:'.length)}`
  return tag
}

// Human age of a ticket: "3m", then "1h 12m". The single most useful triage
// fact on a busy board.
export function ageLabel(iso: string, now: number): string {
  const mins = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60000))
  if (mins < 60) return `${mins}m`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}

export function TicketCard({
  t,
  pushed = false,
  now = Date.now(),
}: {
  t: Ticket
  pushed?: boolean
  // Shared minute tick from the board, so every card's age re-renders together.
  now?: number
}) {
  const [pending, setPending] = useState(false)
  // Compact by default. The guest's quoted line and fine detail live behind a
  // tap, so a busy lane reads as a list of jobs, not a wall of prose.
  const [expanded, setExpanded] = useState(false)
  const DeptIcon = DEPT_ICON[t.dept]
  const itemLine = t.item ? `${t.item}${t.qty ? ` x ${Math.round(t.qty)}` : ''}` : null
  const next = NEXT_STATUS[t.status]
  // The guest is still waiting if they have chased an outstanding (not yet done
  // or cancelled) ticket through ATE. The badge persists between pushes; the
  // pulse is the transient one-shot from a fresh push.
  const waiting = t.nudge_count > 0 && (t.status === 'open' || t.status === 'ack')

  async function advance() {
    if (!next || pending) return
    setPending(true)
    try {
      await setTicketStatus(t.id, next.to)
      // No local state change. The card updates when the backend broadcasts
      // ticket.updated back over the websocket.
    } catch (err) {
      console.error(err)
    } finally {
      setPending(false)
    }
  }

  return (
    <div
      className={`card prio-${t.priority} status-${t.status}${pushed ? ' pushed' : ''}${expanded ? ' expanded' : ''}`}
      onClick={() => setExpanded((v) => !v)}
    >
      {waiting && (
        <div className="waiting">
          <HandWaving size={13} weight="regular" />
          still waiting
          {t.nudge_count > 1 && <span className="waiting-count">{t.nudge_count}x</span>}
        </div>
      )}
      <div className="card-top">
        <DeptIcon className="dept-icon" size={18} weight="regular" />
        <span className="summary">{t.summary}</span>
        {t.priority === 'urgent' && (
          <WarningOctagon className="prio-flag urgent" size={16} weight="regular" />
        )}
        {t.priority === 'high' && (
          <ArrowUp className="prio-flag high" size={16} weight="regular" />
        )}
        <CaretDown className="expand-caret" size={14} weight="regular" />
      </div>

      <div className="meta">
        {itemLine ? `${itemLine} . ` : ''}
        room {t.room}
        {(t.status === 'open' || t.status === 'ack') && (
          <span className="age"> . {ageLabel(t.created_at, now)}</span>
        )}
      </div>

      {t.tags.length > 0 && (
        <div className="chips">
          {t.tags.map((tag) => {
            const kind = classifyTag(tag)
            return (
              <span key={tag} className={`chip ${kind}`}>
                {kind === 'allergy' && <Warning size={12} weight="regular" />}
                {kind === 'escalation' && <Warning size={12} weight="regular" />}
                {kind === 'repeat' && <ArrowsClockwise size={12} weight="regular" />}
                {tagLabel(tag)}
              </span>
            )
          })}
        </div>
      )}

      {expanded && (
        <div className="card-details">
          <div className="utterance">"{t.source_utterance}"</div>
          <div className="meta">priority {t.priority}</div>
        </div>
      )}

      <div className="card-foot">
        <span className={`status-pill ${t.status}`}>
          {t.status === 'done' && <CheckCircle size={13} weight="regular" />}
          {t.status}
        </span>
        {next && (
          <button
            className="advance"
            onClick={(e) => {
              // The action button must never toggle the card open or closed.
              e.stopPropagation()
              advance()
            }}
            disabled={pending}
          >
            {next.label}
          </button>
        )}
      </div>
    </div>
  )
}
