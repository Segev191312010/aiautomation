import { useState, type FormEvent } from 'react'

import { ApiError, describeError, requestToken } from '../api'

type AuthGateProps = {
  notice: string | null
  onAuthenticated: (token: string) => void
}

function describeAuthFailure(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        return 'That secret was rejected. Check JWT_BOOTSTRAP_SECRET in backend/.env.'
      case 403:
        return 'Token requests are only accepted from this Mac (loopback). Open the dashboard locally.'
      case 429: {
        const wait = error.retryAfterSeconds ?? 60
        return `The API is rate-limiting token requests right now. Wait about ${wait} s and try again (other open dashboard tabs keep the limit busy).`
      }
      case 503:
        return `${error.message}. Set JWT_BOOTSTRAP_SECRET in backend/.env and restart the API.`
      default:
        return describeError(error)
    }
  }
  return describeError(error)
}

/** Exchanges the operator's bootstrap secret for a session token. The secret is never stored or logged. */
export function AuthGate({ notice, onAuthenticated }: AuthGateProps) {
  const [secret, setSecret] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    const value = secret.trim()
    if (!value) {
      setError('Paste the bootstrap secret first.')
      return
    }
    setPending(true)
    setError(null)
    try {
      const token = await requestToken(value)
      setSecret('')
      onAuthenticated(token)
    } catch (failure) {
      setError(describeAuthFailure(failure))
      setPending(false)
    }
  }

  return (
    <main className="gate">
      <form className="gate-card" onSubmit={(event) => void submit(event)} aria-labelledby="gate-title">
        <p className="eyebrow">Autotrader V1 · live monitor</p>
        <h1 id="gate-title">Unlock the dashboard</h1>
        <p className="gate-copy">
          Paste the API bootstrap secret once. It is the value of <code>JWT_BOOTSTRAP_SECRET</code> in{' '}
          <code>backend/.env</code> (or the contents of the file named by <code>JWT_BOOTSTRAP_SECRET_FILE</code>).
          It is exchanged for a session token kept only in this browser tab; the secret itself is not saved.
        </p>
        {notice ? (
          <p className="gate-notice" role="status">
            {notice}
          </p>
        ) : null}
        <label className="gate-field">
          <span>Bootstrap secret</span>
          <input
            type="password"
            name="bootstrap-secret"
            autoComplete="off"
            spellCheck={false}
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            disabled={pending}
            autoFocus
          />
        </label>
        {error ? (
          <p className="inline-error" role="alert">
            {error}
          </p>
        ) : null}
        <button type="submit" className="primary-button" disabled={pending}>
          {pending ? 'Checking…' : 'Unlock'}
        </button>
        <p className="gate-foot">Read-only monitor. It shows what the bot does; it cannot place, change, or cancel orders.</p>
      </form>
    </main>
  )
}
