import type { WsMessage } from './types'
import { wsUrl } from './config'

export function openDashboardWs(onMessage: (m: WsMessage) => void): WebSocket {
  const ws = new WebSocket(wsUrl('/ws/dashboard'))
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data) as WsMessage)
    } catch (err) {
      console.error('bad ws frame', err)
    }
  }
  ws.onerror = (err) => console.error('ws error', err)
  return ws
}
