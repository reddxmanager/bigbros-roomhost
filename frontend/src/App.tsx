import { GuestInput } from './components/GuestInput'
import { Dashboard } from './components/Dashboard'

export default function App() {
  return (
    <div style={{ fontFamily: 'system-ui, sans-serif' }}>
      <header style={{ padding: '12px 16px', borderBottom: '1px solid #ccc' }}>
        <strong>Big Bros White Sand</strong>
        <span style={{ color: '#888' }}> &middot; Voice Agent Skeleton (mocked)</span>
      </header>
      <GuestInput />
      <Dashboard />
    </div>
  )
}
