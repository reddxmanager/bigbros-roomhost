// Single source of truth for where the backend lives.
//
// In dev, VITE_API_BASE is empty, so paths stay relative and the Vite dev proxy
// (see vite.config.ts) forwards them to localhost:8000. In production (Netlify),
// VITE_API_BASE is set to the Render URL at build time, so calls go cross-origin
// to the backend. CORS on the backend already allows that.

const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

// Prefix a backend path with the API base. Empty base leaves the path relative,
// so the dev proxy still works and nothing changes for local development.
export function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

// WebSocket URL for a backend path. With an API base set, derive the ws URL from
// it by swapping the http scheme for ws (https -> wss, http -> ws), so it points
// at Render in production. With an empty base, fall back to the current host and
// keep the existing https -> wss protocol logic for local dev behind the proxy.
export function wsUrl(path: string): string {
  if (API_BASE) {
    return `${API_BASE.replace(/^http/, 'ws')}${path}`
  }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}${path}`
}

// ---- Credentials ----
//
// Staff key: gates the dashboard (websocket + status flips). Entered once via
// the key gate in App.tsx and kept in localStorage.
//
// Device token: bound server-side to a room; it IS the tablet's identity.
// Provisioning is "open /tablet?token=XYZ once on the device": the token is
// stored, stripped from the visible flow, and every request carries it as a
// header from then on.

const STAFF_KEY_LS = 'bigbros.staffKey'
const DEVICE_TOKEN_LS = 'bigbros.deviceToken'

export function getStaffKey(): string {
  return localStorage.getItem(STAFF_KEY_LS) ?? ''
}

export function setStaffKey(key: string): void {
  localStorage.setItem(STAFF_KEY_LS, key)
}

export function clearStaffKey(): void {
  localStorage.removeItem(STAFF_KEY_LS)
}

export function getDeviceToken(): string {
  const fromUrl = new URLSearchParams(window.location.search).get('token')
  if (fromUrl) {
    localStorage.setItem(DEVICE_TOKEN_LS, fromUrl)
    return fromUrl
  }
  return localStorage.getItem(DEVICE_TOKEN_LS) ?? ''
}

// Headers for staff HTTP calls (status flips, /rooms, typed /turn).
export function staffHeaders(): Record<string, string> {
  const key = getStaffKey()
  return key ? { 'X-Staff-Key': key } : {}
}

// Headers for tablet HTTP calls. Without a token the backend only answers in
// dev-open mode, which is exactly the intent.
export function deviceHeaders(): Record<string, string> {
  const token = getDeviceToken()
  return token ? { 'X-Device-Token': token } : {}
}
