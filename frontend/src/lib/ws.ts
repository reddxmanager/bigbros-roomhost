import type { WsMessage } from './types'
import { getStaffKey, wsUrl } from './config'

export type WsStatus = 'connected' | 'reconnecting' | 'unauthorized'

export interface DashboardWsHandle {
  close: () => void
}

// Reconnect backoff: quick first retry for a wifi blip, settling to a calm
// ceiling so a dead backend is not hammered.
const BACKOFF_MS = [1000, 2000, 5000, 10000, 30000]

// Auth rejection close code from the backend. Retrying with the same bad key
// forever would be noise; we stop and tell the user instead.
const CLOSE_UNAUTHORIZED = 4401

export function openDashboardWs(
  onMessage: (m: WsMessage) => void,
  onStatus?: (s: WsStatus) => void,
): DashboardWsHandle {
  let ws: WebSocket | null = null
  let attempts = 0
  let closed = false
  let timer: number | undefined

  const connect = () => {
    if (closed) return
    // Browsers cannot set headers on a websocket, so the staff key rides the
    // query string. The backend rejects before accept if it is wrong.
    const key = getStaffKey()
    const url = wsUrl('/ws/dashboard') + (key ? `?key=${encodeURIComponent(key)}` : '')
    ws = new WebSocket(url)

    ws.onopen = () => {
      attempts = 0
      onStatus?.('connected')
    }
    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data) as WsMessage)
      } catch (err) {
        console.error('bad ws frame', err)
      }
    }
    ws.onerror = (err) => console.error('ws error', err)
    ws.onclose = (e) => {
      if (closed) return
      if (e.code === CLOSE_UNAUTHORIZED) {
        onStatus?.('unauthorized')
        return
      }
      // Hotel wifi blips constantly. Reconnect on our own; the server sends a
      // fresh snapshot on every connect, so state self-heals.
      onStatus?.('reconnecting')
      const wait = BACKOFF_MS[Math.min(attempts, BACKOFF_MS.length - 1)]
      attempts += 1
      timer = window.setTimeout(connect, wait)
    }
  }

  connect()

  return {
    close: () => {
      closed = true
      if (timer !== undefined) window.clearTimeout(timer)
      ws?.close()
    },
  }
}
