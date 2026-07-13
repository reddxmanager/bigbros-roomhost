// Simli avatar path. The backend mints a short-lived session token (the API
// key never reaches the browser); simli-client owns the WebRTC from there.
// Reply audio arrives from our backend as base64 mp3 (ElevenLabs, unchanged),
// gets decoded and resampled to PCM16 at 16 kHz here, and streams to Simli,
// which sends lip-synced video and synced audio back into our elements.
//
// Contrast with D-ID (lib/did.ts): no server-held stream, no SDP/ICE relay,
// no warmup-frame gymnastics. Simli renders an honest idle face from the
// first connected frame.

import { SimliClient, LogLevel } from 'simli-client'
import type { Ticket } from './types'
import { apiUrl, deviceHeaders } from './config'
import { devicePost } from './did'

export type { SimliClient }

export async function connectSimli(
  videoEl: HTMLVideoElement,
  audioEl: HTMLAudioElement,
): Promise<SimliClient> {
  const res = await fetch(apiUrl('/tablet/avatar/session'), {
    method: 'POST',
    headers: deviceHeaders(),
  })
  if (!res.ok) throw new Error(`avatar session failed: ${res.status}`)
  const { session_token, ice_servers } = await res.json()
  if (!session_token) throw new Error('avatar session failed: no token')
  const client = new SimliClient(
    session_token,
    videoEl,
    audioEl,
    ice_servers && ice_servers.length ? ice_servers : null,
    LogLevel.ERROR,
  )
  await client.start()
  return client
}

// Decode base64 mp3 to PCM16 mono at 16 kHz, the one format Simli eats.
// decodeAudioData handles the mp3; an OfflineAudioContext does the resample.
export async function mp3ToPcm16(b64: string): Promise<Uint8Array> {
  const raw = atob(b64)
  const bytes = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)

  const AC =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
  const probe = new AC()
  const decoded = await probe.decodeAudioData(bytes.buffer)
  await probe.close()

  const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000)
  const src = offline.createBufferSource()
  src.buffer = decoded
  src.connect(offline.destination)
  src.start()
  const rendered = await offline.startRendering()

  const ch = rendered.getChannelData(0)
  const pcm = new Int16Array(ch.length)
  for (let i = 0; i < ch.length; i++) {
    const s = Math.max(-1, Math.min(1, ch[i]))
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return new Uint8Array(pcm.buffer)
}

// Feed audio in modest chunks so the SDK's buffer stays happy.
const CHUNK_BYTES = 6000

export async function speakB64(client: SimliClient, audioB64: string): Promise<void> {
  const pcm = await mp3ToPcm16(audioB64)
  for (let i = 0; i < pcm.length; i += CHUNK_BYTES) {
    client.sendAudioData(pcm.subarray(i, Math.min(i + CHUNK_BYTES, pcm.length)))
  }
}

// ---- Backend calls (same endpoints as D-ID, minus the stream ids) ----

export async function simliAck(
  room: string,
  language: string,
): Promise<{ spoken: boolean; audio_b64?: string | null }> {
  const res = await devicePost('/tablet/ack', { room, language })
  if (!res.ok) throw new Error(`ack failed: ${res.status}`)
  return res.json()
}

export async function simliTurn(
  room: string,
  text: string,
  language: string,
): Promise<{
  reply: string
  tickets: Ticket[]
  language: string
  spoken: boolean
  sentiment: string
  audio_b64?: string | null
}> {
  const res = await devicePost('/tablet/turn', { room, text, language })
  if (!res.ok) throw new Error(`turn failed: ${res.status}`)
  return res.json()
}

export async function simliSpeak(
  text: string,
  language: string,
): Promise<{ spoken: boolean; audio_b64?: string | null }> {
  const res = await devicePost('/tablet/speak', { text, language })
  if (!res.ok) throw new Error(`speak failed: ${res.status}`)
  return res.json()
}
