/**
 * Circuit Breaker Status Panel — shows consecutive AI failure tracking
 * and allows manual reset after human review.
 *
 * Inspired by nofx's auto-protection mechanism: when AI fails 3+ times
 * consecutively, emergency stop auto-activates to protect positions.
 */
import { useEffect, useState, useCallback } from 'react'
import {
  fetchCircuitBreakerStatus,
  resetCircuitBreaker,
  type CircuitBreakerStatus,
} from '@/services/api'

export default function CircuitBreakerPanel() {
  const [status, setStatus] = useState<CircuitBreakerStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await fetchCircuitBreakerStatus()
      setStatus(data)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  // Auto-refresh every 30 seconds
  useEffect(() => {
    const interval = setInterval(() => { void load() }, 30_000)
    return () => clearInterval(interval)
  }, [load])

  async function handleReset() {
    setLoading(true)
    try {
      await resetCircuitBreaker()
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reset failed')
    } finally {
      setLoading(false)
    }
  }

  if (!status) return null

  const hasFailures = Object.values(status.counts).some((c) => c > 0)
  const maxCount = Math.max(0, ...Object.values(status.counts))

  return (
    <div
      className={`rounded-2xl border p-5 ${
        status.breaker_tripped
          ? 'border-red-300 bg-red-50'
          : hasFailures
            ? 'border-amber-200 bg-amber-50'
            : 'border-[var(--border)] bg-white'
      }`}
    >
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)] flex items-center gap-2">
            {status.breaker_tripped ? (
              <span className="inline-block h-2 w-2 rounded-full bg-red-500 animate-pulse" />
            ) : hasFailures ? (
              <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />
            ) : (
              <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
            )}
            AI Circuit Breaker
          </h2>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">
            {status.breaker_tripped
              ? 'TRIPPED: Emergency stop auto-activated due to consecutive AI failures'
              : hasFailures
                ? `${maxCount}/${status.threshold} consecutive failures — monitoring`
                : 'All AI systems operating normally'}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {status.breaker_tripped && (
            <button
              type="button"
              onClick={() => void handleReset()}
              disabled={loading}
              className="rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50"
            >
              {loading ? 'Resetting...' : 'Reset Breaker'}
            </button>
          )}
        </div>
      </div>

      {hasFailures && (
        <div className="mt-3 space-y-1">
          {Object.entries(status.counts)
            .filter(([, count]) => count > 0)
            .map(([source, count]) => (
              <div key={source} className="flex items-center justify-between text-xs">
                <span className="text-[var(--text-secondary)] font-mono">{source}</span>
                <div className="flex items-center gap-2">
                  <div className="flex gap-0.5">
                    {Array.from({ length: status.threshold }).map((_, i) => (
                      <div
                        key={i}
                        className={`h-1.5 w-4 rounded-full ${
                          i < count ? 'bg-red-400' : 'bg-gray-200'
                        }`}
                      />
                    ))}
                  </div>
                  <span className="text-[var(--text-muted)] w-12 text-right">
                    {count}/{status.threshold}
                  </span>
                </div>
              </div>
            ))}
        </div>
      )}

      {error && (
        <p className="mt-2 text-xs text-red-600">{error}</p>
      )}
    </div>
  )
}
