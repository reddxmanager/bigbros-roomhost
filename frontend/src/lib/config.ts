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
