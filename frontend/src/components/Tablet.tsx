import { useEffect, useRef, useState } from 'react'
import {
  ForkKnife,
  BeerStein,
  Broom,
  Wrench,
  Bell,
  Warning,
  CheckCircle,
  Translate,
  HandWaving,
  Info,
} from '@phosphor-icons/react'
import type { Dept, RoomState, Ticket } from '../lib/types'
import {
  connectAvatar,
  closeStream,
  tabletAck,
  tabletTurn,
  tabletSpeak,
  type StreamHandle,
} from '../lib/did'

// TEMP wake-render audit logging. Timestamps are ms since page load, so the
// console shows the exact ordering of overlay mount/onload, frame detection,
// and the photo-to-live swap. Strip in the cleanup pass.
function wlog(msg: string, extra?: unknown) {
  const t = Math.round(performance.now())
  if (extra === undefined) console.log(`[wake +${t}ms] ${msg}`)
  else console.log(`[wake +${t}ms] ${msg}`, extra)
}

const CANONICAL =
  "Hey, the aircon in our room's not really cooling, and can we get two more San Migs and some extra rice? Oh, what time's breakfast?"

const DEPT_ICON: Record<Dept, typeof ForkKnife> = {
  kitchen: ForkKnife,
  bar: BeerStein,
  housekeeping: Broom,
  maintenance: Wrench,
  frontdesk: Bell,
}

// Warm, guest-facing phrasing for each lane. No fake ETA, per the spec rule.
const DEPT_RECEIPT: Record<Dept, string> = {
  kitchen: 'Sent to the kitchen',
  bar: 'Sent to the bar',
  housekeeping: 'On the way',
  maintenance: 'On the way',
  frontdesk: 'Front desk notified',
}

function roomFromUrl(): string {
  return new URLSearchParams(window.location.search).get('room') || '4'
}

// Demo language control. Default English so a non-Korean judge follows the
// whole interaction; ?lang=ko (or the on-screen toggle) starts the Korean
// showcase. The reply and caption follow this; tickets stay English regardless.
type Lang = 'en' | 'ko'
function langFromUrl(): Lang {
  return new URLSearchParams(window.location.search).get('lang') === 'ko' ? 'ko' : 'en'
}

function Receipt({ t }: { t: Ticket }) {
  const Icon = DEPT_ICON[t.dept]
  const allergy = t.tags.find((tag) => tag.startsWith('allergy:'))
  const itemLine = t.item ? `${t.item}${t.qty ? ` x ${Math.round(t.qty)}` : ''}` : null
  return (
    <div className="receipt">
      <Icon size={22} weight="regular" className="receipt-icon" />
      <div className="receipt-body">
        <div className="receipt-summary">{t.summary}</div>
        <div className="receipt-sub">
          {itemLine ? `${itemLine} . ` : ''}
          {DEPT_RECEIPT[t.dept]}
        </div>
        {allergy && (
          <span className="receipt-allergy">
            <Warning size={13} weight="regular" />
            Shellfish-safe noted
          </span>
        )}
      </div>
    </div>
  )
}

export function Tablet() {
  const room = roomFromUrl()
  const videoRef = useRef<HTMLVideoElement>(null)
  const handleRef = useRef<StreamHandle | null>(null)
  // The MediaStream id we have already bound, so the track event firing more
  // than once does not re-assign srcObject or double-register the frame
  // callback (that re-assignment is what caused the play() AbortError).
  const boundStreamIdRef = useRef<string | null>(null)
  // The swap to live video is "armed" only once a speak has been requested.
  // D-ID's idle/warmup frame (a real 150x150 placeholder, not the presenter)
  // arrives before any speak; we must not treat it as the presenter, or the
  // host photo gets pulled onto a blank frame. We swap on the first frame that
  // arrives after a speak is initiated, which is the actual presenter.
  const swapArmedRef = useRef(false)

  const [suite, setSuite] = useState('')
  const [language, setLanguage] = useState<Lang>(langFromUrl)
  const [status, setStatus] = useState<'idle' | 'connecting' | 'live' | 'thinking'>('idle')
  const [text, setText] = useState(CANONICAL)
  const [caption, setCaption] = useState('')
  const [receipts, setReceipts] = useState<Ticket[]>([])
  const [infoLine, setInfoLine] = useState('')
  // Reactive-face cue from the brain's decision: a soft warm/cool tint on the
  // caption. Defaults neutral (no tint) so a missing signal never breaks the UI.
  const [sentiment, setSentiment] = useState<'concern' | 'warm' | 'neutral'>('neutral')
  const [error, setError] = useState('')
  // The presenter image, for the static-face fallback.
  const [sourceUrl, setSourceUrl] = useState('')
  // A real D-ID video frame has painted into the tile (idle/warmup or first
  // speak). Until then we cover the tile with the static host photo.
  const [frameLive, setFrameLive] = useState(false)
  // The static host photo has loaded, so a face is on the tile even before any
  // real frame. Either of these means the guest is looking at a face, not a
  // grey ring, which is what gates LIVE and the enabled Ask button.
  const [stillLoaded, setStillLoaded] = useState(false)
  const faceVisible = frameLive || stillLoaded

  const bindTrack = (stream: MediaStream) => {
    const v = videoRef.current
    if (!v) return
    // The track event can fire more than once for the same stream (audio +
    // video tracks both carry it). Bind, and register the frame callback, once
    // per stream. Re-assigning srcObject is what aborted the prior play().
    if (boundStreamIdRef.current === stream.id) {
      wlog('bindTrack: duplicate event for same stream, skipping', { streamId: stream.id })
      return
    }
    boundStreamIdRef.current = stream.id
    v.srcObject = stream
    const vtracks = stream.getVideoTracks().map((t) => ({
      id: t.id,
      muted: t.muted,
      readyState: t.readyState,
    }))
    wlog('bindTrack: srcObject set', { streamId: stream.id, videoTracks: vtracks })
    // play() can reject with AbortError if a load interrupts it. That race is
    // benign and self-heals, so swallow it rather than surfacing an error.
    v.play().then(
      () => wlog('video.play() resolved'),
      (e: DOMException) => {
        if (e?.name === 'AbortError') wlog('video.play() AbortError (benign, ignored)')
        else wlog('video.play() rejected', String(e))
      },
    )
    // Swap to live video only on the first frame that arrives AFTER a speak is
    // requested. D-ID's idle/warmup frame is a real presented frame but a blank
    // placeholder, not the presenter, so we ignore frames until the swap is
    // armed and keep re-registering to catch the first real presenter frame.
    const vv = v as HTMLVideoElement & {
      requestVideoFrameCallback?: (
        cb: (now: number, meta: Record<string, number>) => void,
      ) => number
    }
    if (typeof vv.requestVideoFrameCallback === 'function') {
      wlog('registering requestVideoFrameCallback')
      let ignored = 0
      const onFrame = (_now: number, meta: Record<string, number>) => {
        if (swapArmedRef.current) {
          wlog('rVFC frame after speak -> setFrameLive(true)', {
            width: meta?.width,
            height: meta?.height,
            mediaTime: meta?.mediaTime,
            presentedFrames: meta?.presentedFrames,
            ignoredWarmupFrames: ignored,
          })
          setFrameLive(true)
          return
        }
        ignored += 1
        if (ignored === 1) {
          wlog('rVFC idle/warmup frame ignored (no speak yet)', {
            width: meta?.width,
            height: meta?.height,
            presentedFrames: meta?.presentedFrames,
          })
        }
        vv.requestVideoFrameCallback(onFrame)
      }
      vv.requestVideoFrameCallback(onFrame)
    } else {
      // Fallback: timeupdate fires as playback advances. Gate it the same way,
      // so the swap still waits for a post-speak frame.
      wlog('rVFC unsupported -> using timeupdate fallback')
      const onTime = () => {
        if (!swapArmedRef.current) return
        wlog('timeupdate after speak -> setFrameLive(true)', {
          readyState: v.readyState,
          videoWidth: v.videoWidth,
          currentTime: v.currentTime,
        })
        setFrameLive(true)
        v.removeEventListener('timeupdate', onTime)
      }
      v.addEventListener('timeupdate', onTime)
    }
  }

  // TEMP wake-render audit. Log each flag flip and the overlay show/hide, with
  // the video's frame state at the moment the overlay is pulled, so we can see
  // whether the swap fires before a real frame exists. Strip in cleanup.
  const overlayVisible = status !== 'idle' && !!sourceUrl && !frameLive
  useEffect(() => {
    wlog(`frameLive -> ${frameLive}`)
  }, [frameLive])
  useEffect(() => {
    wlog(`stillLoaded -> ${stillLoaded}`)
  }, [stillLoaded])
  useEffect(() => {
    wlog(`faceVisible -> ${faceVisible}`)
  }, [faceVisible])
  useEffect(() => {
    const v = videoRef.current
    wlog(`overlay visible = ${overlayVisible}; video underneath`, {
      readyState: v?.readyState,
      videoWidth: v?.videoWidth,
      videoHeight: v?.videoHeight,
      currentTime: v?.currentTime,
      paused: v?.paused,
    })
  }, [overlayVisible])

  useEffect(() => {
    fetch('/rooms')
      .then((r) => r.json())
      .then((rooms: RoomState[]) => {
        const found = rooms.find((x) => x.room === room)
        if (found) setSuite(found.suite_name)
      })
      .catch(() => {})
  }, [room])

  // Load the host photo up front so the tile shows a resting host before wake,
  // not an empty panel. This is the same presenter image the stream will use,
  // so the resting photo and the on-wake still match.
  useEffect(() => {
    fetch('/tablet/avatar')
      .then((r) => r.json())
      .then((d: { source_url?: string }) => {
        if (d.source_url) setSourceUrl(d.source_url)
      })
      .catch(() => {})
  }, [])

  // Tear down the stream when the page unloads or the component unmounts, so we
  // never leave a dead peer behind for the next session.
  useEffect(() => {
    const onUnload = () => {
      if (handleRef.current) closeStream(handleRef.current)
    }
    window.addEventListener('beforeunload', onUnload)
    return () => {
      window.removeEventListener('beforeunload', onUnload)
      if (handleRef.current) closeStream(handleRef.current)
    }
  }, [])

  async function wake() {
    if (status !== 'idle') return
    setStatus('connecting')
    setError('')
    setFrameLive(false)
    setStillLoaded(false)
    // Fresh session: nothing bound yet, and the swap stays disarmed until the
    // guest's first speak so the warmup frame cannot pull the photo.
    boundStreamIdRef.current = null
    swapArmedRef.current = false
    try {
      // Close any prior stream before opening a fresh one.
      if (handleRef.current) {
        await closeStream(handleRef.current)
        handleRef.current = null
      }
      const handle = await connectAvatar(bindTrack)
      handleRef.current = handle
      setSourceUrl(handle.sourceUrl)
      setStatus('live')
    } catch (err) {
      setError(`Could not reach the avatar. ${String(err)}`)
      setStatus('idle')
    }
  }

  // Re-handshake a fresh, connected stream. Used to recover from a stale session.
  async function reconnect(): Promise<StreamHandle> {
    if (handleRef.current) {
      await closeStream(handleRef.current)
      handleRef.current = null
    }
    // The fresh stream has its own warmup frame. Cover the tile with the photo
    // again and disarm the swap until the caller re-speaks, so the new warmup
    // frame cannot pull the photo onto a blank frame.
    setFrameLive(false)
    swapArmedRef.current = false
    const handle = await connectAvatar(bindTrack)
    handleRef.current = handle
    setSourceUrl(handle.sourceUrl)
    return handle
  }

  async function send() {
    let handle = handleRef.current
    if (!handle || !text.trim() || status === 'thinking') return
    setStatus('thinking')
    setError('')
    setCaption('')
    setReceipts([])
    setInfoLine('')
    setSentiment('neutral')
    try {
      // Arm the photo->live swap: from here the next presenter frame is a real
      // speak (the ack), not the idle warmup, so it is safe to reveal the video.
      swapArmedRef.current = true
      // Latency mask: the avatar acknowledges immediately, before the heavy turn.
      // The ack is best effort. If the session went stale, re-handshake once and
      // retry, but never block the real turn on the ack.
      const ack = await tabletAck(handle, room, language)
      if (!ack.spoken) {
        handle = await reconnect()
        swapArmedRef.current = true
        await tabletAck(handle, room, language).catch(() => {})
      }

      const result = await tabletTurn(handle, room, text, language)
      // Tickets land regardless of whether the avatar voiced the reply.
      if (result.tickets.length === 0 && result.reply) {
        // A pure answer-in-place turn surfaces on the quiet info line.
        setInfoLine(result.reply)
      } else {
        setCaption(result.reply)
        setReceipts(result.tickets)
      }
      // Reactive-face cue. Defaults neutral if the field is absent.
      const s = result.sentiment
      setSentiment(s === 'concern' || s === 'warm' ? s : 'neutral')

      // If the stream was stale at speak time, re-handshake and voice the same
      // reply once more without re-running the brain.
      if (!result.spoken && result.reply) {
        handle = await reconnect()
        swapArmedRef.current = true
        const retry = await tabletSpeak(handle, result.reply, result.language)
        if (!retry.spoken) {
          setError('The avatar could not speak that, but your request was still sent.')
        }
      }
    } catch (err) {
      setError(`Something went wrong. ${String(err)}`)
    } finally {
      setStatus('live')
    }
  }

  const speaking = status === 'thinking'

  return (
    <div className="tablet">
      <header className="tablet-header">
        <div className="tablet-wordmark">
          <span className="brand">Big Bros White Sand</span>
          {suite && <span className="suite">{suite}</span>}
        </div>
        <div className="lang-toggle" role="group" aria-label="Reply language">
          <Translate size={16} weight="regular" className="lang-icon" />
          <button
            className={language === 'en' ? 'active' : ''}
            onClick={() => setLanguage('en')}
          >
            English
          </button>
          <button
            className={language === 'ko' ? 'active' : ''}
            onClick={() => setLanguage('ko')}
          >
            한국어
          </button>
        </div>
      </header>

      <div className="tablet-body">
        {/* The avatar and the caption read as one warm unit: a teak stage that
            holds the host's face and what they are saying. */}
        <div className="avatar-stage">
          <div className="avatar-tile">
            <video ref={videoRef} className="avatar-video" playsInline autoPlay />
            {/* The host photo covers the tile from before wake until a real D-ID
                frame paints: the resting host (dimmed) before wake, then the
                still face after connect, so the guest never sees an empty void
                or a blank grey ring. */}
            {sourceUrl && !frameLive && (
              <img
                className={`avatar-still${status === 'idle' ? ' resting' : ''}`}
                src={sourceUrl}
                alt=""
                onLoad={() => {
                  wlog('overlay IMG onLoad -> setStillLoaded(true)')
                  setStillLoaded(true)
                }}
              />
            )}
            {status === 'idle' && <div className="avatar-rest-scrim" />}
            {(status === 'live' || status === 'thinking') && faceVisible ? (
              <span className="live-dot" aria-label="Avatar live">
                <span className="blink" /> Live
              </span>
            ) : null}
            {status === 'idle' && (
              <button className="wake" onClick={wake}>
                <HandWaving size={20} weight="regular" />
                Tap to wake your host
              </button>
            )}
            {status === 'connecting' && (
              <div className="wake-note">Waking your host...</div>
            )}
            {speaking && (
              <span className="speaking-dots" aria-label="Speaking">
                <span /> <span /> <span />
              </span>
            )}
          </div>

          <div
            className={`stage-speech${
              caption && sentiment !== 'neutral' ? ` tone-${sentiment}` : ''
            }`}
          >
            {caption && <p className="caption">{caption}</p>}
            {infoLine && (
              <p className="info-line">
                <Info size={18} weight="regular" />
                {infoLine}
              </p>
            )}
            {!caption && !infoLine && status !== 'idle' && (
              <p className="stage-hint">
                {status === 'thinking' ? (
                  'One moment...'
                ) : (
                  <>
                    <CheckCircle size={18} weight="regular" />
                    Ready when you are.
                  </>
                )}
              </p>
            )}
          </div>
        </div>

        <div className="tablet-side">
          {receipts.length > 0 && (
            <div className="receipts">
              {receipts.map((t) => (
                <Receipt key={t.id} t={t} />
              ))}
            </div>
          )}

          <div className="tablet-input">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              placeholder="Say what you need..."
            />
            <button
              className="ask"
              onClick={send}
              disabled={
                status === 'idle' ||
                status === 'connecting' ||
                status === 'thinking' ||
                !faceVisible
              }
            >
              {status === 'thinking' ? 'Sorting that out...' : 'Ask'}
            </button>
          </div>
          {error && <div className="tablet-error">{error}</div>}
        </div>
      </div>

      <footer className="listening-footer">
        Big Bros 2026 · Powered by D-ID and ElevenLabs
      </footer>
    </div>
  )
}
