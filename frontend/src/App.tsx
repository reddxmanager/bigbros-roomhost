import { useState } from 'react'
import { GuestInput } from './components/GuestInput'
import { Dashboard } from './components/Dashboard'
import { Tablet } from './components/Tablet'
import { getStaffKey, setStaffKey } from './lib/config'

// Minimal staff gate. The dashboard shows guest rooms, raw utterances, and
// allergy tags, so it does not render until a staff key is stored. The key is
// checked server-side on the websocket and every status flip; this gate only
// keeps the honest path clean. Wrong key: the socket is refused and staff see
// an empty board, so re-entry is a paste away via ?reset=1.
function KeyGate({ onSet }: { onSet: () => void }) {
  const [value, setValue] = useState('')
  return (
    <div className="key-gate">
      <h2>Staff access</h2>
      <p>Enter the staff key to open the dashboard.</p>
      <input
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && value.trim()) {
            setStaffKey(value.trim())
            onSet()
          }
        }}
        placeholder="Staff key"
        autoFocus
      />
      <button
        onClick={() => {
          if (value.trim()) {
            setStaffKey(value.trim())
            onSet()
          }
        }}
      >
        Open dashboard
      </button>
    </div>
  )
}

export default function App() {
  // Tiny path switch, no router dependency. /tablet is the warm guest surface,
  // everything else is the calm staff dashboard.
  const [, bump] = useState(0)

  if (window.location.pathname.startsWith('/tablet')) {
    return <Tablet />
  }

  if (new URLSearchParams(window.location.search).get('reset') === '1') {
    setStaffKey('')
  }

  if (!getStaffKey()) {
    return <KeyGate onSet={() => bump((n) => n + 1)} />
  }

  return (
    <div>
      <header className="app-header">
        <span className="brand">Big Bros White Sand</span>
        <span className="sub">Staff Dashboard</span>
      </header>
      {/* The typed demo input is a dev tool. It never ships to production:
          anyone who could see it could inject guest turns into any room. */}
      {import.meta.env.DEV && <GuestInput />}
      <Dashboard />
    </div>
  )
}
