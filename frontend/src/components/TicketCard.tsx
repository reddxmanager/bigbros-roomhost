import { useState } from 'react'
import {
  ArrowUp,
  ArrowsClockwise,
  Bell,
  BeerStein,
  Broom,
  CheckCircle,
  ForkKnife,
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

export function TicketCard({ t }: { t: Ticket }) {
  const [pending, setPending] = useState(false)
  const DeptIcon = DEPT_ICON[t.dept]
  const itemLine = t.item ? `${t.item}${t.qty ? ` x ${Math.round(t.qty)}` : ''}` : null
  const next = NEXT_STATUS[t.status]

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
    <div className={`card prio-${t.priority} status-${t.status}`}>
      <div className="card-top">
        <DeptIcon className="dept-icon" size={18} weight="regular" />
        <span className="summary">{t.summary}</span>
        {t.priority === 'urgent' && (
          <WarningOctagon className="prio-flag urgent" size={16} weight="regular" />
        )}
        {t.priority === 'high' && (
          <ArrowUp className="prio-flag high" size={16} weight="regular" />
        )}
      </div>

      <div className="meta">
        {itemLine ? `${itemLine} . ` : ''}
        priority {t.priority} . room {t.room}
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

      <div className="utterance">"{t.source_utterance}"</div>

      <div className="card-foot">
        <span className={`status-pill ${t.status}`}>
          {t.status === 'done' && <CheckCircle size={13} weight="regular" />}
          {t.status}
        </span>
        {next && (
          <button className="advance" onClick={advance} disabled={pending}>
            {next.label}
          </button>
        )}
      </div>
    </div>
  )
}
