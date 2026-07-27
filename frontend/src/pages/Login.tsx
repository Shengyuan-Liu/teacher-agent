import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api } from '@/lib/api'
import { useAuth } from '@/lib/auth'

export default function Login() {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const setAuth = useAuth((s) => s.setAuth)
  const navigate = useNavigate()

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (mode === 'register') {
        await api.register(email, password)
      }
      const tokens = await api.login(email, password)
      setAuth(tokens.access_token, email)
      navigate('/')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Network error, please retry')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-shell">
      <h1>TeacherAgent</h1>
      <p className="subtitle">Bring your own material and turn it into a course you can question.</p>
      <form className="card auth-card" onSubmit={submit}>
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? 'Working…' : mode === 'login' ? 'Log in' : 'Register & log in'}
        </button>
        <button
          type="button"
          className="link-button"
          onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
        >
          {mode === 'login' ? 'No account? Register' : 'Have an account? Log in'}
        </button>
      </form>
    </main>
  )
}
