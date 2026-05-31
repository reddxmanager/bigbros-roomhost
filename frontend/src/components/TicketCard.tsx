import type { Ticket } from '../lib/types'

export function TicketCard({ t }: { t: Ticket }) {
  const itemLine = t.item ? `${t.item}${t.qty ? ` x${t.qty}` : ''}` : null
  return (
    <div
      style={{
        border: '1px solid #aaa',
        padding: 8,
        marginBottom: 8,
        background: '#fff',
      }}
    >
      <div style={{ fontWeight: 600 }}>{t.summary}</div>
      <div style={{ fontSize: 12, color: '#666', marginTop: 2 }}>
        {itemLine ? <>{itemLine} &middot; </> : null}
        priority: {t.priority} &middot; room {t.room}
      </div>
      {t.tags.length > 0 && (
        <div style={{ fontSize: 12, marginTop: 4 }}>
          {t.tags.map((tag) => (
            <span
              key={tag}
              style={{ marginRight: 6, background: '#eee', padding: '1px 6px' }}
            >
              {tag}
            </span>
          ))}
        </div>
      )}
      <div
        style={{
          fontSize: 11,
          color: '#999',
          marginTop: 6,
          fontStyle: 'italic',
        }}
      >
        &ldquo;{t.source_utterance}&rdquo;
      </div>
    </div>
  )
}
