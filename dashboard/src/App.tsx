import { useCallback, useState } from 'react'

import { clearToken, loadToken, saveToken } from './api'
import { AuthGate } from './components/AuthGate'
import { LiveDashboard } from './components/LiveDashboard'

const EXPIRED_NOTICE = 'Your session expired or was rejected. Paste the bootstrap secret again.'

/** Read-only live monitor: an auth gate in front of the polling dashboard. */
export default function App() {
  const [token, setToken] = useState<string | null>(() => loadToken())
  const [notice, setNotice] = useState<string | null>(null)

  const handleAuthenticated = useCallback((value: string) => {
    saveToken(value)
    setNotice(null)
    setToken(value)
  }, [])

  const handleUnauthorized = useCallback(() => {
    clearToken()
    setNotice(EXPIRED_NOTICE)
    setToken(null)
  }, [])

  const handleLock = useCallback(() => {
    clearToken()
    setNotice(null)
    setToken(null)
  }, [])

  if (token === null) {
    return <AuthGate notice={notice} onAuthenticated={handleAuthenticated} />
  }
  return <LiveDashboard token={token} onUnauthorized={handleUnauthorized} onLock={handleLock} />
}
