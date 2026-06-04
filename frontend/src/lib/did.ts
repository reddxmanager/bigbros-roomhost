// D-ID WebRTC client. The browser owns the peer connection and the media, but
// never sees the D-ID or ElevenLabs keys. The backend creates the stream and
// relays our SDP answer and ICE candidates. Audio is heard through the avatar
// video track, since D-ID lip-syncs our ElevenLabs audio and streams it back.

import type { Ticket } from './types'
import { apiUrl } from './config'

export interface StreamHandle {
  id: string
  sessionId: string
  pc: RTCPeerConnection
  // The presenter image, shown as a static face in the tile until a real D-ID
  // frame paints, so the guest never sees a blank grey ring on wake.
  sourceUrl: string
}

const CONNECT_TIMEOUT_MS = 15000

export async function connectAvatar(
  onTrack: (stream: MediaStream) => void,
): Promise<StreamHandle> {
  const res = await fetch(apiUrl('/tablet/stream/new'), { method: 'POST' })
  if (!res.ok) throw new Error(`stream create failed: ${res.status}`)
  const { id, session_id, offer, ice_servers, source_url } = await res.json()

  const pc = new RTCPeerConnection({ iceServers: ice_servers })

  pc.addEventListener('track', (e) => {
    if (e.streams && e.streams[0]) onTrack(e.streams[0])
  })

  pc.addEventListener('icecandidate', (e) => {
    const c = e.candidate
    fetch(apiUrl(`/tablet/stream/${id}/ice`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id,
        candidate: c ? c.candidate : null,
        sdpMid: c ? c.sdpMid : null,
        sdpMLineIndex: c ? c.sdpMLineIndex : null,
      }),
    }).catch(() => {})
  })

  // Resolve once the peer is actually connected, so the host is visible and the
  // stream is live before any speak is sent. Falls through after a timeout so a
  // slow ICE path does not hang the UI forever.
  const connected = new Promise<void>((resolve) => {
    let done = false
    const finish = () => {
      if (!done) {
        done = true
        resolve()
      }
    }
    pc.addEventListener('iceconnectionstatechange', () => {
      if (pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
        finish()
      }
    })
    setTimeout(finish, CONNECT_TIMEOUT_MS)
  })

  await pc.setRemoteDescription(offer)
  const answer = await pc.createAnswer()
  await pc.setLocalDescription(answer)

  await fetch(apiUrl(`/tablet/stream/${id}/sdp`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id, answer }),
  })

  await connected
  return { id, sessionId: session_id, pc, sourceUrl: source_url || '' }
}

export async function closeStream(handle: StreamHandle): Promise<void> {
  try {
    await fetch(apiUrl(`/tablet/stream/${handle.id}/close`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: handle.sessionId }),
    })
  } catch {
    // best effort
  }
  try {
    handle.pc.close()
  } catch {
    // best effort
  }
}

export async function tabletListen(
  blob: Blob,
): Promise<{ text: string; language: string }> {
  const form = new FormData()
  form.append('clip', blob, 'clip.webm')
  const res = await fetch(apiUrl('/tablet/listen'), { method: 'POST', body: form })
  if (!res.ok) throw new Error(`listen failed: ${res.status}`)
  return res.json()
}

export async function tabletAck(
  handle: StreamHandle,
  room: string,
  language: string,
): Promise<{ spoken: boolean }> {
  const res = await fetch(apiUrl('/tablet/ack'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ room, stream_id: handle.id, session_id: handle.sessionId, language }),
  })
  if (!res.ok) throw new Error(`ack failed: ${res.status}`)
  return res.json()
}

export async function tabletTurn(
  handle: StreamHandle,
  room: string,
  text: string,
  language: string,
): Promise<{
  reply: string
  tickets: Ticket[]
  language: string
  spoken: boolean
  sentiment: string
}> {
  const res = await fetch(apiUrl('/tablet/turn'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      room,
      text,
      stream_id: handle.id,
      session_id: handle.sessionId,
      language,
    }),
  })
  if (!res.ok) throw new Error(`turn failed: ${res.status}`)
  return res.json()
}

export async function tabletSpeak(
  handle: StreamHandle,
  text: string,
  language: string,
): Promise<{ spoken: boolean }> {
  const res = await fetch(apiUrl('/tablet/speak'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      stream_id: handle.id,
      session_id: handle.sessionId,
      text,
      language,
    }),
  })
  if (!res.ok) throw new Error(`speak failed: ${res.status}`)
  return res.json()
}
