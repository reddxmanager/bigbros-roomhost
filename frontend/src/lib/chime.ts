// The audible half of a push. When the guest chases an outstanding ticket
// through ATE, the dashboard plays a soft two-note cue so a manager glancing
// away still registers "someone is still waiting on this." Deliberately calm,
// not an alarm: low gain, gentle sines, quick fades to avoid any click. No
// audio asset, synthesized with the Web Audio API.

let ctx: AudioContext | null = null

function context(): AudioContext | null {
  if (typeof window === 'undefined') return null
  const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  if (!ctx) ctx = new Ctor()
  return ctx
}

function note(audio: AudioContext, freq: number, start: number, dur: number): void {
  const osc = audio.createOscillator()
  const gain = audio.createGain()
  osc.type = 'sine'
  osc.frequency.value = freq
  // Short attack and release so the cue is felt, not heard as a beep.
  gain.gain.setValueAtTime(0, start)
  gain.gain.linearRampToValueAtTime(0.06, start + 0.02)
  gain.gain.linearRampToValueAtTime(0, start + dur)
  osc.connect(gain).connect(audio.destination)
  osc.start(start)
  osc.stop(start + dur)
}

export function playWaitingChime(): void {
  const audio = context()
  if (!audio) return
  // Browsers suspend the context until a user gesture. The first staff click on
  // the dashboard resumes it; before that the cue is simply silent.
  if (audio.state === 'suspended') void audio.resume()
  const t = audio.currentTime
  note(audio, 660, t, 0.16)
  note(audio, 880, t + 0.14, 0.2)
}
