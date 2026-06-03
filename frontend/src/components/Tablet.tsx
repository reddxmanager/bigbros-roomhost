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
  Microphone,
} from '@phosphor-icons/react'
import type { Dept, RoomState, Ticket } from '../lib/types'
import {
  connectAvatar,
  closeStream,
  tabletAck,
  tabletTurn,
  tabletSpeak,
  tabletListen,
  type StreamHandle,
} from '../lib/did'

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

// Model A states. idle = asleep, connecting = handshake, greeting = the host's
// opening speak, live = awake with the mic CLOSED (tap to speak), thinking =
// processing one turn. The mic is open only during a recording, which is a
// separate boolean, never a resting state.
type Status = 'idle' | 'connecting' | 'greeting' | 'live' | 'thinking'

// The host's opening line on wake. Default English; the guest's detected
// language takes over once they speak. No em dashes.
const GREETING: Record<Lang, string> = {
  en: 'Hi there, how can I help you?',
  ko: '안녕하세요, 무엇을 도와드릴까요?',
}

// Return to the asleep resting state after this long with no interaction.
// Silence is the dismissal; there is no voice goodbye command.
const IDLE_MS = 45000

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
  // Push-to-talk capture. The mic is opened only inside startRecording and the
  // tracks are stopped the instant recording ends, so the mic is never live
  // outside a deliberate tap. Privacy by design (one suite is a honeymoon suite).
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const mediaStreamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const audioCtxRef = useRef<AudioContext | null>(null)
  const rafRef = useRef<number | null>(null)
  // Idle-to-sleep timer. Runs only while awake (live, mic closed).
  const idleTimerRef = useRef<number | null>(null)

  const [suite, setSuite] = useState('')
  const [language, setLanguage] = useState<Lang>(langFromUrl)
  const [recording, setRecording] = useState(false)
  const [status, setStatus] = useState<Status>('idle')
  const [text, setText] = useState('')
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
    if (boundStreamIdRef.current === stream.id) return
    boundStreamIdRef.current = stream.id
    v.srcObject = stream
    // play() can reject with AbortError if a load interrupts it. That race is
    // benign and self-heals, so swallow it rather than surfacing an error.
    v.play().catch(() => {})
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
      const onFrame = () => {
        if (swapArmedRef.current) {
          setFrameLive(true)
          return
        }
        vv.requestVideoFrameCallback(onFrame)
      }
      vv.requestVideoFrameCallback(onFrame)
    } else {
      // Fallback: timeupdate fires as playback advances. Gate it the same way,
      // so the swap still waits for a post-speak frame.
      const onTime = () => {
        if (!swapArmedRef.current) return
        setFrameLive(true)
        v.removeEventListener('timeupdate', onTime)
      }
      v.addEventListener('timeupdate', onTime)
    }
  }

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
      // Never leave the mic open or a sleep timer pending on unmount.
      stopMicTracks()
      clearIdleTimer()
    }
  }, [])

  function clearIdleTimer() {
    if (idleTimerRef.current !== null) {
      clearTimeout(idleTimerRef.current)
      idleTimerRef.current = null
    }
  }
  function startIdleTimer() {
    clearIdleTimer()
    idleTimerRef.current = window.setTimeout(() => sleep(), IDLE_MS)
  }
  function bumpIdle() {
    if (status === 'live' && !recording) startIdleTimer()
  }

  async function wake() {
    if (status !== 'idle') return
    setStatus('connecting')
    setError('')
    setCaption('')
    setReceipts([])
    setInfoLine('')
    setSentiment('neutral')
    setFrameLive(false)
    setStillLoaded(false)
    // Fresh session: nothing bound yet, and the swap stays disarmed until the
    // greeting (the first speak) so the warmup frame cannot pull the photo.
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
      // The host greets first. The mic stays CLOSED; it only opens later on a
      // deliberate tap. The greeting is also the first speak, so it drives the
      // photo->presenter swap and covers the connect with no silent stare.
      await greet(handle)
    } catch (err) {
      setError(`Could not reach the avatar. ${String(err)}`)
      setStatus('idle')
    }
  }

  // Speak the opening line. This is the first presenter speak, so it arms and
  // performs the photo->presenter swap exactly like a turn's ack does.
  async function greet(handle: StreamHandle): Promise<void> {
    setStatus('greeting')
    swapArmedRef.current = true
    let h = handle
    const r = await tabletSpeak(h, GREETING[language], language)
    if (!r.spoken) {
      // Stream went stale before the greeting (rare on a fresh stream): one
      // re-handshake and retry, mirroring the turn stale-session recovery.
      h = await reconnect()
      swapArmedRef.current = true
      await tabletSpeak(h, GREETING[language], language).catch(() => {})
    }
    setStatus('live')
    startIdleTimer()
  }

  // Idle dismissal: return to the fully asleep resting state. The stream is
  // closed (mic was already closed), so nothing listens until the next wake.
  function sleep() {
    clearIdleTimer()
    stopMicTracks()
    if (handleRef.current) {
      closeStream(handleRef.current)
      handleRef.current = null
    }
    boundStreamIdRef.current = null
    swapArmedRef.current = false
    setRecording(false)
    setFrameLive(false)
    setCaption('')
    setReceipts([])
    setInfoLine('')
    setSentiment('neutral')
    setStatus('idle')
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

  // Latency-mask ack, with the stale-session recovery preserved exactly from the
  // original send(). Returns the (possibly re-handshaked) handle. Split out so
  // the voice path can fire the ack the instant the clip is sent, covering the
  // Scribe round-trip as well as brain + TTS.
  async function fireAck(handle: StreamHandle): Promise<StreamHandle> {
    // Arm the photo->live swap: from here the next presenter frame is a real
    // speak (the ack), not the idle warmup, so it is safe to reveal the video.
    swapArmedRef.current = true
    // The ack is best effort. If the session went stale, re-handshake once and
    // retry, but never block the real turn on the ack.
    const ack = await tabletAck(handle, room, language)
    if (!ack.spoken) {
      handle = await reconnect()
      swapArmedRef.current = true
      await tabletAck(handle, room, language).catch(() => {})
    }
    return handle
  }

  // The brain turn: route, surface tickets/caption/sentiment, and re-speak on a
  // stale stream. No ack here (the caller fired it). Stale-session logic is
  // preserved byte-for-byte from the original send().
  async function runBrainTurn(
    handle: StreamHandle,
    transcript: string,
    lang: string,
  ): Promise<void> {
    const result = await tabletTurn(handle, room, transcript, lang)
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
  }

  // Text mode: type and Ask. Same flow as before, now via the shared helpers.
  async function send() {
    let handle = handleRef.current
    if (!handle || !text.trim() || status !== 'live' || recording) return
    clearIdleTimer()
    setStatus('thinking')
    setError('')
    setCaption('')
    setReceipts([])
    setInfoLine('')
    setSentiment('neutral')
    try {
      handle = await fireAck(handle)
      await runBrainTurn(handle, text, language)
    } catch (err) {
      setError(`Something went wrong. ${String(err)}`)
    } finally {
      setStatus('live')
      startIdleTimer()
    }
  }

  // Voice mode: a captured clip. Fire the ack first so it covers Scribe + brain
  // + TTS, transcribe with Scribe, show what it heard in the box as it sends
  // (one motion), and let the detected language drive the reply.
  async function sendVoice(blob: Blob) {
    let handle = handleRef.current
    if (!handle || status === 'thinking') return
    setStatus('thinking')
    setError('')
    setCaption('')
    setReceipts([])
    setInfoLine('')
    setSentiment('neutral')
    try {
      handle = await fireAck(handle)
      const heard = await tabletListen(blob)
      // Scribe's detected language flows straight through, so whatever the guest
      // speaks (Tagalog, Korean, anything Scribe tags) drives the spoken reply.
      // Tickets stay English regardless, enforced in the brain prompt. Default
      // English if the tag is missing.
      const detected = heard.language || 'en'
      // Show the transcript. Reflect en/ko on the demo toggle when that is what
      // was detected; any other language still flows to the turn untouched,
      // without needing a toggle entry (no language picker UI).
      setText(heard.text)
      if (detected === 'en' || detected === 'ko') setLanguage(detected)
      if (!heard.text.trim()) {
        setError('I did not catch that. Please try again, or use Text mode.')
        return
      }
      await runBrainTurn(handle, heard.text, detected)
    } catch (err) {
      setError(`Something went wrong. ${String(err)}`)
    } finally {
      setStatus('live')
      startIdleTimer()
    }
  }

  // Close the mic and tear down the analyser. Called the instant recording ends
  // and on unmount, so the mic is never live outside a deliberate tap.
  function stopMicTracks() {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop())
      mediaStreamRef.current = null
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {})
      audioCtxRef.current = null
    }
  }

  async function startRecording() {
    // Only from the awake, mic-closed state, and only on a deliberate tap.
    if (status !== 'live' || recording || !faceVisible) return
    clearIdleTimer()
    setError('')
    let stream: MediaStream
    try {
      // The browser prompts here. The mic opens only on this deliberate tap.
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setError('Microphone access is needed for Voice. You can use Text mode instead.')
      return
    }
    mediaStreamRef.current = stream
    chunksRef.current = []
    const mr = new MediaRecorder(stream)
    mediaRecorderRef.current = mr
    mr.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunksRef.current.push(e.data)
    }
    mr.onstop = () => {
      // Close the mic immediately, then send the clip.
      stopMicTracks()
      setRecording(false)
      const blob = new Blob(chunksRef.current, { type: mr.mimeType || 'audio/webm' })
      if (blob.size > 0) sendVoice(blob)
    }
    mr.start()
    setRecording(true)
    startSilenceWatch(stream)
  }

  function stopRecording() {
    const mr = mediaRecorderRef.current
    if (mr && mr.state !== 'inactive') {
      mr.stop() // fires onstop, which closes the mic and sends the clip
    } else {
      stopMicTracks()
      setRecording(false)
    }
  }

  // Auto-stop after a longer silence than the turn endpoint (~1.8s), and only
  // once the guest has actually spoken, so a pause before speaking or a natural
  // mid-sentence pause does not clip the take.
  function startSilenceWatch(stream: MediaStream) {
    const ctx = new AudioContext()
    audioCtxRef.current = ctx
    // Created after an await, so it can start suspended. Resume it, or the
    // analyser receives no samples and the silence auto-stop never fires.
    ctx.resume().catch(() => {})
    const source = ctx.createMediaStreamSource(stream)
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 2048
    source.connect(analyser)
    const buf = new Uint8Array(analyser.fftSize)
    const SILENCE_RMS = 0.012
    const SILENCE_MS = 1800
    let spokenYet = false
    let silentSince: number | null = null
    const tick = () => {
      analyser.getByteTimeDomainData(buf)
      let sum = 0
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128
        sum += v * v
      }
      const rms = Math.sqrt(sum / buf.length)
      const now = performance.now()
      if (rms >= SILENCE_RMS) {
        spokenYet = true
        silentSince = null
      } else if (spokenYet) {
        if (silentSince === null) silentSince = now
        else if (now - silentSince >= SILENCE_MS) {
          stopRecording()
          return
        }
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }

  const speaking = status === 'greeting' || status === 'thinking'

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
                onLoad={() => setStillLoaded(true)}
              />
            )}
            {status === 'idle' && <div className="avatar-rest-scrim" />}
            {(status === 'greeting' || status === 'live' || status === 'thinking') &&
            faceVisible ? (
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
            {/* Push-to-talk lives on the host. Awake + mic closed shows the
                invite; an active capture shows the stop control. */}
            {status === 'live' && !recording && (
              <button className="tile-speak" onClick={startRecording} disabled={!faceVisible}>
                <Microphone size={22} weight="regular" />
                Tap to speak
              </button>
            )}
            {recording && (
              <button className="tile-speak recording" onClick={stopRecording}>
                <span className="rec-dot" />
                Listening... tap to stop
              </button>
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
            {!caption &&
              !infoLine &&
              (status === 'thinking' || (status === 'live' && !recording)) && (
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

          {/* Voice is primary (tap to speak on the host). Typing is the small,
              quiet fallback if the mic misbehaves on camera. */}
          <div className="type-fallback">
            <input
              className="type-input"
              type="text"
              value={text}
              onChange={(e) => {
                setText(e.target.value)
                bumpIdle()
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') send()
              }}
              placeholder="or type your request..."
              disabled={status !== 'live' || recording}
            />
            <button
              className="type-send"
              onClick={send}
              disabled={status !== 'live' || recording || !text.trim()}
            >
              Send
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
