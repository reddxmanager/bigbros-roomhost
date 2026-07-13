import { useState } from 'react'
import type { TurnResponse } from '../lib/types'
import { apiUrl, staffHeaders } from '../lib/config'

const CANONICAL =
  "Hey, the aircon in our room's not really cooling, and can we get two more San Migs and some extra rice? Oh, what time's breakfast?"

export function GuestInput() {
  const [text, setText] = useState(CANONICAL)
  const [room, setRoom] = useState('4')
  const [reply, setReply] = useState('')
  const [pending, setPending] = useState(false)

  async function send() {
    if (!text.trim()) return
    setPending(true)
    setReply('')
    try {
      const res = await fetch(apiUrl('/turn'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...staffHeaders() },
        body: JSON.stringify({ room, text }),
      })
      if (!res.ok) {
        setReply(`Backend error: ${res.status}`)
        return
      }
      const data = (await res.json()) as TurnResponse
      setReply(data.reply ?? '')
    } catch (err) {
      setReply(`Request failed: ${String(err)}`)
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="guest-input">
      <div className="row">
        <label htmlFor="room-select">Room</label>
        <select
          id="room-select"
          value={room}
          onChange={(e) => setRoom(e.target.value)}
        >
          <option value="4">4 . Family Suite . ko . allergy:shellfish</option>
          <option value="honeymoon">Honeymoon Suite . en . anniversary</option>
        </select>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
      />
      <div className="row" style={{ marginTop: 10, marginBottom: 0 }}>
        <button className="send" onClick={send} disabled={pending}>
          {pending ? 'Sending...' : 'Send'}
        </button>
        {reply && <span className="reply">Avatar: "{reply}"</span>}
      </div>
    </div>
  )
}
