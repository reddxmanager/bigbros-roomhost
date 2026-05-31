import type { WsMessage } from './types'

export function openDashboardWs(onMessage: (m: WsMessage) => void): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const ws = new WebSocket(`${proto}//${location.host}/ws/dashboard`)
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
