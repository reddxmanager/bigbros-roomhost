import { GuestInput } from './components/GuestInput'
import { Dashboard } from './components/Dashboard'
import { Tablet } from './components/Tablet'

export default function App() {
  // Tiny path switch, no router dependency. /tablet is the warm guest surface,
  // everything else is the calm staff dashboard. The dashboard is untouched.
  if (window.location.pathname.startsWith('/tablet')) {
    return <Tablet />
  }

  return (
    <div>
      <header className="app-header">
        <span className="brand">Big Bros White Sand</span>
        <span className="sub">Staff Dashboard</span>
      </header>
      <GuestInput />
      <Dashboard />
    </div>
  )
}
