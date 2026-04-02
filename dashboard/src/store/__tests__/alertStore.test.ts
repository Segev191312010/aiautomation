import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useAlertStore } from '@/store/alertStore'
import type { Alert, AlertFiredEvent, AlertHistory, AlertStats } from '@/types'

// ---------------------------------------------------------------------------
// Module-level mock for @/services/api
// vi.mock is hoisted so this declaration runs before any imports are resolved.
// ---------------------------------------------------------------------------
vi.mock('@/services/api', () => ({
  fetchAlerts:       vi.fn(),
  fetchAlertHistory: vi.fn(),
  fetchAlertStats:   vi.fn(),
}))

// Import the mocked module so we can configure return values per-test.
import * as api from '@/services/api'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeAlert(id: string, symbol = 'AAPL'): Alert {
  return {
    id,
    user_id: 'user-1',
    name: `Alert ${id}`,
    symbol,
    condition: { indicator: 'RSI', params: { length: 14 }, operator: '<', value: 30 },
    alert_type: 'recurring',
    cooldown_minutes: 60,
    enabled: true,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function makeHistory(id: string, symbol = 'AAPL'): AlertHistory {
  return {
    id,
    alert_id: `alert-${id}`,
    alert_name: `Alert ${id}`,
    symbol,
    condition_summary: 'RSI(14) < 30',
    price_at_trigger: 175.5,
    fired_at: '2026-01-01T10:00:00Z',
  }
}

function makeFiredEvent(alertId: string, symbol = 'AAPL'): AlertFiredEvent {
  return {
    type: 'alert_fired',
    alert_id: alertId,
    name: `Alert ${alertId}`,
    symbol,
    condition_summary: 'RSI(14) < 30',
    price: 174.0,
    timestamp: '2026-01-01T10:05:00Z',
  }
}

// ---------------------------------------------------------------------------
// Reset store to pristine state before each test
// ---------------------------------------------------------------------------

beforeEach(() => {
  useAlertStore.setState({
    alerts: [],
    history: [],
    unreadCount: 0,
    recentFired: [],
    loading: false,
    alertStats: null,
  })
})

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

describe('alertStore — initial state', () => {
  it('starts with an empty alerts array', () => {
    expect(useAlertStore.getState().alerts).toEqual([])
  })

  it('starts with an empty history array', () => {
    expect(useAlertStore.getState().history).toEqual([])
  })

  it('starts with loading=false', () => {
    expect(useAlertStore.getState().loading).toBe(false)
  })

  it('starts with unreadCount=0', () => {
    expect(useAlertStore.getState().unreadCount).toBe(0)
  })

  it('starts with an empty recentFired array', () => {
    expect(useAlertStore.getState().recentFired).toEqual([])
  })

  it('starts with alertStats=null', () => {
    expect(useAlertStore.getState().alertStats).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// setAlerts
// ---------------------------------------------------------------------------

describe('alertStore — setAlerts', () => {
  it('replaces the alerts array', () => {
    const alerts = [makeAlert('a1'), makeAlert('a2')]
    useAlertStore.getState().setAlerts(alerts)
    expect(useAlertStore.getState().alerts).toHaveLength(2)
    expect(useAlertStore.getState().alerts[0].id).toBe('a1')
    expect(useAlertStore.getState().alerts[1].id).toBe('a2')
  })

  it('can clear alerts by setting an empty array', () => {
    useAlertStore.getState().setAlerts([makeAlert('a1')])
    useAlertStore.getState().setAlerts([])
    expect(useAlertStore.getState().alerts).toHaveLength(0)
  })

  it('does not affect history, loading, or unreadCount', () => {
    useAlertStore.setState({ unreadCount: 3, loading: true })
    useAlertStore.getState().setAlerts([makeAlert('a1')])
    const s = useAlertStore.getState()
    expect(s.unreadCount).toBe(3)
    expect(s.loading).toBe(true)
    expect(s.history).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// setHistory
// ---------------------------------------------------------------------------

describe('alertStore — setHistory', () => {
  it('replaces the history array', () => {
    const history = [makeHistory('h1'), makeHistory('h2'), makeHistory('h3')]
    useAlertStore.getState().setHistory(history)
    expect(useAlertStore.getState().history).toHaveLength(3)
  })

  it('stores each history item with the correct fields', () => {
    useAlertStore.getState().setHistory([makeHistory('h1', 'MSFT')])
    const h = useAlertStore.getState().history[0]
    expect(h.symbol).toBe('MSFT')
    expect(h.condition_summary).toBe('RSI(14) < 30')
  })

  it('can clear history', () => {
    useAlertStore.getState().setHistory([makeHistory('h1')])
    useAlertStore.getState().setHistory([])
    expect(useAlertStore.getState().history).toHaveLength(0)
  })

  it('does not affect alerts or unreadCount', () => {
    useAlertStore.setState({ alerts: [makeAlert('a1')], unreadCount: 2 })
    useAlertStore.getState().setHistory([makeHistory('h1')])
    expect(useAlertStore.getState().alerts).toHaveLength(1)
    expect(useAlertStore.getState().unreadCount).toBe(2)
  })
})

// ---------------------------------------------------------------------------
// pushFired
// ---------------------------------------------------------------------------

describe('alertStore — pushFired', () => {
  it('prepends the event to recentFired', () => {
    const event = makeFiredEvent('a1')
    useAlertStore.getState().pushFired(event)
    const { recentFired } = useAlertStore.getState()
    expect(recentFired).toHaveLength(1)
    expect(recentFired[0].alert_id).toBe('a1')
  })

  it('increments unreadCount by 1 each call', () => {
    useAlertStore.getState().pushFired(makeFiredEvent('a1'))
    useAlertStore.getState().pushFired(makeFiredEvent('a2'))
    useAlertStore.getState().pushFired(makeFiredEvent('a3'))
    expect(useAlertStore.getState().unreadCount).toBe(3)
  })

  it('most recent event appears first (prepend order)', () => {
    useAlertStore.getState().pushFired(makeFiredEvent('first'))
    useAlertStore.getState().pushFired(makeFiredEvent('second'))
    const { recentFired } = useAlertStore.getState()
    expect(recentFired[0].alert_id).toBe('second')
    expect(recentFired[1].alert_id).toBe('first')
  })

  it('caps recentFired at 20 items', () => {
    for (let i = 0; i < 25; i++) {
      useAlertStore.getState().pushFired(makeFiredEvent(`a${i}`))
    }
    expect(useAlertStore.getState().recentFired).toHaveLength(20)
  })

  it('retains the 20 most recent events when over the cap', () => {
    for (let i = 0; i < 25; i++) {
      useAlertStore.getState().pushFired(makeFiredEvent(`a${i}`))
    }
    // Most recent push was a24, so recentFired[0] should be a24
    expect(useAlertStore.getState().recentFired[0].alert_id).toBe('a24')
    // The 20th entry (index 19) should be a5 (pushed before a6..a24)
    expect(useAlertStore.getState().recentFired[19].alert_id).toBe('a5')
  })

  it('does not affect existing alerts or history', () => {
    useAlertStore.setState({ alerts: [makeAlert('a1')] })
    useAlertStore.getState().pushFired(makeFiredEvent('a99'))
    expect(useAlertStore.getState().alerts).toHaveLength(1)
    expect(useAlertStore.getState().history).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// markRead
// ---------------------------------------------------------------------------

describe('alertStore — markRead', () => {
  it('resets unreadCount to 0', () => {
    useAlertStore.setState({ unreadCount: 7 })
    useAlertStore.getState().markRead()
    expect(useAlertStore.getState().unreadCount).toBe(0)
  })

  it('is idempotent when unreadCount is already 0', () => {
    useAlertStore.getState().markRead()
    expect(useAlertStore.getState().unreadCount).toBe(0)
  })

  it('does not affect recentFired or alerts', () => {
    useAlertStore.setState({
      unreadCount: 5,
      recentFired: [makeFiredEvent('a1')],
      alerts: [makeAlert('a2')],
    })
    useAlertStore.getState().markRead()
    expect(useAlertStore.getState().recentFired).toHaveLength(1)
    expect(useAlertStore.getState().alerts).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// updateAlert
// ---------------------------------------------------------------------------

describe('alertStore — updateAlert', () => {
  it('replaces the matching alert by id', () => {
    const a1 = makeAlert('a1', 'AAPL')
    const a2 = makeAlert('a2', 'MSFT')
    useAlertStore.setState({ alerts: [a1, a2] })

    const updated = { ...a1, name: 'Updated Name', enabled: false }
    useAlertStore.getState().updateAlert(updated)

    const alerts = useAlertStore.getState().alerts
    expect(alerts).toHaveLength(2)
    expect(alerts[0].name).toBe('Updated Name')
    expect(alerts[0].enabled).toBe(false)
    // Second alert must be untouched
    expect(alerts[1].id).toBe('a2')
    expect(alerts[1].symbol).toBe('MSFT')
  })

  it('updates the second alert without affecting the first', () => {
    useAlertStore.setState({ alerts: [makeAlert('a1'), makeAlert('a2')] })
    const updated = { ...makeAlert('a2'), name: 'Changed a2' }
    useAlertStore.getState().updateAlert(updated)

    const alerts = useAlertStore.getState().alerts
    expect(alerts[0].name).toBe('Alert a1')
    expect(alerts[1].name).toBe('Changed a2')
  })

  it('is a no-op when no alert matches the id', () => {
    useAlertStore.setState({ alerts: [makeAlert('a1')] })
    useAlertStore.getState().updateAlert(makeAlert('nonexistent'))
    expect(useAlertStore.getState().alerts).toHaveLength(1)
    expect(useAlertStore.getState().alerts[0].id).toBe('a1')
  })

  it('does not mutate history, unreadCount, or recentFired', () => {
    useAlertStore.setState({
      alerts: [makeAlert('a1')],
      history: [makeHistory('h1')],
      unreadCount: 4,
      recentFired: [makeFiredEvent('e1')],
    })
    useAlertStore.getState().updateAlert({ ...makeAlert('a1'), name: 'X' })

    expect(useAlertStore.getState().history).toHaveLength(1)
    expect(useAlertStore.getState().unreadCount).toBe(4)
    expect(useAlertStore.getState().recentFired).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// removeAlert
// ---------------------------------------------------------------------------

describe('alertStore — removeAlert', () => {
  it('removes the alert with the given id', () => {
    useAlertStore.setState({ alerts: [makeAlert('a1'), makeAlert('a2')] })
    useAlertStore.getState().removeAlert('a1')
    const { alerts } = useAlertStore.getState()
    expect(alerts).toHaveLength(1)
    expect(alerts[0].id).toBe('a2')
  })

  it('removes the second alert by id', () => {
    useAlertStore.setState({ alerts: [makeAlert('a1'), makeAlert('a2')] })
    useAlertStore.getState().removeAlert('a2')
    const { alerts } = useAlertStore.getState()
    expect(alerts).toHaveLength(1)
    expect(alerts[0].id).toBe('a1')
  })

  it('removes the only alert leaving an empty array', () => {
    useAlertStore.setState({ alerts: [makeAlert('a1')] })
    useAlertStore.getState().removeAlert('a1')
    expect(useAlertStore.getState().alerts).toHaveLength(0)
  })

  it('is a no-op when the id does not exist', () => {
    useAlertStore.setState({ alerts: [makeAlert('a1'), makeAlert('a2')] })
    useAlertStore.getState().removeAlert('nonexistent')
    expect(useAlertStore.getState().alerts).toHaveLength(2)
  })

  it('does not affect history, unreadCount, or recentFired', () => {
    useAlertStore.setState({
      alerts: [makeAlert('a1')],
      history: [makeHistory('h1')],
      unreadCount: 2,
      recentFired: [makeFiredEvent('e1')],
    })
    useAlertStore.getState().removeAlert('a1')

    expect(useAlertStore.getState().history).toHaveLength(1)
    expect(useAlertStore.getState().unreadCount).toBe(2)
    expect(useAlertStore.getState().recentFired).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// setLoading
// ---------------------------------------------------------------------------

describe('alertStore — setLoading', () => {
  it('sets loading to true', () => {
    useAlertStore.getState().setLoading(true)
    expect(useAlertStore.getState().loading).toBe(true)
  })

  it('sets loading back to false', () => {
    useAlertStore.setState({ loading: true })
    useAlertStore.getState().setLoading(false)
    expect(useAlertStore.getState().loading).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Compound / interaction scenarios
// ---------------------------------------------------------------------------

describe('alertStore — compound scenarios', () => {
  it('unreadCount reflects total pushFired calls across multiple symbols', () => {
    useAlertStore.getState().pushFired(makeFiredEvent('a1', 'AAPL'))
    useAlertStore.getState().pushFired(makeFiredEvent('a2', 'MSFT'))
    useAlertStore.getState().pushFired(makeFiredEvent('a3', 'NVDA'))
    expect(useAlertStore.getState().unreadCount).toBe(3)
    useAlertStore.getState().markRead()
    expect(useAlertStore.getState().unreadCount).toBe(0)
    // Firing again after markRead increments from zero
    useAlertStore.getState().pushFired(makeFiredEvent('a4'))
    expect(useAlertStore.getState().unreadCount).toBe(1)
  })

  it('updateAlert after removeAlert does not resurrect the removed alert', () => {
    const a1 = makeAlert('a1')
    useAlertStore.setState({ alerts: [a1, makeAlert('a2')] })
    useAlertStore.getState().removeAlert('a1')
    // Attempting to update the removed alert should be a no-op
    useAlertStore.getState().updateAlert({ ...a1, name: 'Ghost' })
    const { alerts } = useAlertStore.getState()
    expect(alerts).toHaveLength(1)
    expect(alerts[0].id).toBe('a2')
  })

  it('setAlerts then removeAlert leaves correct subset', () => {
    useAlertStore.getState().setAlerts([makeAlert('a1'), makeAlert('a2'), makeAlert('a3')])
    useAlertStore.getState().removeAlert('a2')
    const { alerts } = useAlertStore.getState()
    expect(alerts).toHaveLength(2)
    expect(alerts.map((a) => a.id)).toEqual(['a1', 'a3'])
  })
})

// ---------------------------------------------------------------------------
// loadAlerts — async, calls fetchAlerts API
// ---------------------------------------------------------------------------

describe('alertStore — loadAlerts', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('sets loading=true then loading=false on success', async () => {
    const mockAlerts = [makeAlert('a1'), makeAlert('a2')]
    vi.mocked(api.fetchAlerts).mockResolvedValue(mockAlerts)

    const loadingStates: boolean[] = []
    const unsub = useAlertStore.subscribe((s) => loadingStates.push(s.loading))

    await useAlertStore.getState().loadAlerts()
    unsub()

    // At least one intermediate true followed by false
    expect(loadingStates).toContain(true)
    expect(useAlertStore.getState().loading).toBe(false)
  })

  it('populates store.alerts with the API response', async () => {
    const mockAlerts = [makeAlert('a1', 'AAPL'), makeAlert('a2', 'MSFT')]
    vi.mocked(api.fetchAlerts).mockResolvedValue(mockAlerts)

    await useAlertStore.getState().loadAlerts()

    const { alerts } = useAlertStore.getState()
    expect(alerts).toHaveLength(2)
    expect(alerts[0].id).toBe('a1')
    expect(alerts[1].symbol).toBe('MSFT')
  })

  it('replaces any existing alerts with the fresh API response', async () => {
    useAlertStore.setState({ alerts: [makeAlert('old')] })
    vi.mocked(api.fetchAlerts).mockResolvedValue([makeAlert('new1'), makeAlert('new2')])

    await useAlertStore.getState().loadAlerts()

    const { alerts } = useAlertStore.getState()
    expect(alerts).toHaveLength(2)
    expect(alerts[0].id).toBe('new1')
  })

  it('clears loading and does not throw when the API rejects', async () => {
    vi.mocked(api.fetchAlerts).mockRejectedValue(new Error('network error'))

    await expect(useAlertStore.getState().loadAlerts()).resolves.toBeUndefined()
    expect(useAlertStore.getState().loading).toBe(false)
  })

  it('leaves existing alerts unchanged when the API rejects', async () => {
    const existing = [makeAlert('a1')]
    useAlertStore.setState({ alerts: existing })
    vi.mocked(api.fetchAlerts).mockRejectedValue(new Error('500'))

    await useAlertStore.getState().loadAlerts()

    // On error the store sets loading false but does NOT overwrite alerts
    expect(useAlertStore.getState().alerts).toEqual(existing)
  })

  it('handles an empty array response from the API', async () => {
    vi.mocked(api.fetchAlerts).mockResolvedValue([])

    await useAlertStore.getState().loadAlerts()

    expect(useAlertStore.getState().alerts).toHaveLength(0)
    expect(useAlertStore.getState().loading).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// loadHistory — async, calls fetchAlertHistory API
// ---------------------------------------------------------------------------

describe('alertStore — loadHistory', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('populates store.history with the API response', async () => {
    const mockHistory = [makeHistory('h1', 'AAPL'), makeHistory('h2', 'NVDA')]
    vi.mocked(api.fetchAlertHistory).mockResolvedValue(mockHistory)

    await useAlertStore.getState().loadHistory()

    const { history } = useAlertStore.getState()
    expect(history).toHaveLength(2)
    expect(history[0].id).toBe('h1')
    expect(history[1].symbol).toBe('NVDA')
  })

  it('replaces existing history with the fresh API response', async () => {
    useAlertStore.setState({ history: [makeHistory('old')] })
    vi.mocked(api.fetchAlertHistory).mockResolvedValue([makeHistory('new1')])

    await useAlertStore.getState().loadHistory()

    expect(useAlertStore.getState().history).toHaveLength(1)
    expect(useAlertStore.getState().history[0].id).toBe('new1')
  })

  it('handles an empty history response', async () => {
    vi.mocked(api.fetchAlertHistory).mockResolvedValue([])

    await useAlertStore.getState().loadHistory()

    expect(useAlertStore.getState().history).toHaveLength(0)
  })

  it('does not throw and leaves history untouched when the API rejects', async () => {
    const existing = [makeHistory('h1')]
    useAlertStore.setState({ history: existing })
    vi.mocked(api.fetchAlertHistory).mockRejectedValue(new Error('offline'))

    await expect(useAlertStore.getState().loadHistory()).resolves.toBeUndefined()
    // Silently swallowed — existing history preserved
    expect(useAlertStore.getState().history).toEqual(existing)
  })

  it('does not affect alerts or unreadCount', async () => {
    useAlertStore.setState({ alerts: [makeAlert('a1')], unreadCount: 3 })
    vi.mocked(api.fetchAlertHistory).mockResolvedValue([makeHistory('h1')])

    await useAlertStore.getState().loadHistory()

    expect(useAlertStore.getState().alerts).toHaveLength(1)
    expect(useAlertStore.getState().unreadCount).toBe(3)
  })
})

// ---------------------------------------------------------------------------
// fetchAlertStats — success path
// ---------------------------------------------------------------------------

describe('alertStore — fetchAlertStats (success)', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('stores the API-returned stats object', async () => {
    const mockStats: AlertStats = {
      total_today: 5,
      total_week:  22,
      total_month: 80,
      top_symbols: [{ symbol: 'AAPL', count: 10 }, { symbol: 'MSFT', count: 7 }],
      daily_counts: [{ date: '2026-03-31', count: 5 }],
    }
    vi.mocked(api.fetchAlertStats).mockResolvedValue(mockStats)

    await useAlertStore.getState().fetchAlertStats()

    const { alertStats } = useAlertStore.getState()
    expect(alertStats).not.toBeNull()
    expect(alertStats!.total_today).toBe(5)
    expect(alertStats!.total_week).toBe(22)
    expect(alertStats!.total_month).toBe(80)
    expect(alertStats!.top_symbols).toHaveLength(2)
    expect(alertStats!.top_symbols[0].symbol).toBe('AAPL')
  })

  it('replaces previously cached stats with the fresh response', async () => {
    const first: AlertStats = {
      total_today: 1, total_week: 1, total_month: 1,
      top_symbols: [], daily_counts: [],
    }
    const second: AlertStats = {
      total_today: 9, total_week: 40, total_month: 150,
      top_symbols: [{ symbol: 'NVDA', count: 20 }], daily_counts: [],
    }
    vi.mocked(api.fetchAlertStats).mockResolvedValueOnce(first).mockResolvedValueOnce(second)

    await useAlertStore.getState().fetchAlertStats()
    await useAlertStore.getState().fetchAlertStats()

    expect(useAlertStore.getState().alertStats!.total_today).toBe(9)
  })

  it('does not affect alerts or history', async () => {
    useAlertStore.setState({ alerts: [makeAlert('a1')], history: [makeHistory('h1')] })
    vi.mocked(api.fetchAlertStats).mockResolvedValue({
      total_today: 0, total_week: 0, total_month: 0,
      top_symbols: [], daily_counts: [],
    })

    await useAlertStore.getState().fetchAlertStats()

    expect(useAlertStore.getState().alerts).toHaveLength(1)
    expect(useAlertStore.getState().history).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// fetchAlertStats — client-side fallback (API throws)
// ---------------------------------------------------------------------------

describe('alertStore — fetchAlertStats (fallback)', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('computes stats from history when the API is offline', async () => {
    vi.mocked(api.fetchAlertStats).mockRejectedValue(new Error('503'))

    const now = Date.now()
    const todayStr   = new Date(now - 1_000).toISOString()          // 1 s ago
    const weekStr    = new Date(now - 3 * 86_400_000).toISOString() // 3 days ago
    const oldStr     = new Date(now - 35 * 86_400_000).toISOString() // 35 days ago (outside month)

    const history: AlertHistory[] = [
      { ...makeHistory('h1', 'AAPL'), fired_at: todayStr },
      { ...makeHistory('h2', 'AAPL'), fired_at: todayStr },
      { ...makeHistory('h3', 'MSFT'), fired_at: weekStr },
      { ...makeHistory('h4', 'NVDA'), fired_at: oldStr },
    ]
    useAlertStore.setState({ history })

    await useAlertStore.getState().fetchAlertStats()

    const stats = useAlertStore.getState().alertStats!
    expect(stats).not.toBeNull()
    // 2 events within the last day
    expect(stats.total_today).toBe(2)
    // 3 events within the last 7 days (today + weekStr; oldStr is excluded)
    expect(stats.total_week).toBe(3)
    // 3 events within the last 30 days (oldStr at 35 days is excluded)
    expect(stats.total_month).toBe(3)
  })

  it('ranks top_symbols by descending count', async () => {
    vi.mocked(api.fetchAlertStats).mockRejectedValue(new Error('offline'))

    const recentIso = new Date(Date.now() - 1_000).toISOString()
    const history: AlertHistory[] = [
      { ...makeHistory('h1', 'AAPL'), fired_at: recentIso },
      { ...makeHistory('h2', 'AAPL'), fired_at: recentIso },
      { ...makeHistory('h3', 'AAPL'), fired_at: recentIso },
      { ...makeHistory('h4', 'MSFT'), fired_at: recentIso },
      { ...makeHistory('h5', 'MSFT'), fired_at: recentIso },
      { ...makeHistory('h6', 'NVDA'), fired_at: recentIso },
    ]
    useAlertStore.setState({ history })

    await useAlertStore.getState().fetchAlertStats()

    const { top_symbols } = useAlertStore.getState().alertStats!
    expect(top_symbols[0].symbol).toBe('AAPL')
    expect(top_symbols[0].count).toBe(3)
    expect(top_symbols[1].symbol).toBe('MSFT')
    expect(top_symbols[1].count).toBe(2)
    expect(top_symbols[2].symbol).toBe('NVDA')
    expect(top_symbols[2].count).toBe(1)
  })

  it('caps top_symbols at 5 entries', async () => {
    vi.mocked(api.fetchAlertStats).mockRejectedValue(new Error('offline'))

    const recentIso = new Date(Date.now() - 1_000).toISOString()
    const history: AlertHistory[] = ['A','B','C','D','E','F','G'].map((sym, i) => ({
      ...makeHistory(`h${i}`, sym), fired_at: recentIso,
    }))
    useAlertStore.setState({ history })

    await useAlertStore.getState().fetchAlertStats()

    expect(useAlertStore.getState().alertStats!.top_symbols).toHaveLength(5)
  })

  it('produces daily_counts covering exactly 14 days', async () => {
    vi.mocked(api.fetchAlertStats).mockRejectedValue(new Error('offline'))
    useAlertStore.setState({ history: [] })

    await useAlertStore.getState().fetchAlertStats()

    const { daily_counts } = useAlertStore.getState().alertStats!
    expect(daily_counts).toHaveLength(14)
    // Dates must be in ascending order (oldest first)
    for (let i = 1; i < daily_counts.length; i++) {
      expect(daily_counts[i].date >= daily_counts[i - 1].date).toBe(true)
    }
  })

  it('counts history events into the correct day bucket', async () => {
    vi.mocked(api.fetchAlertStats).mockRejectedValue(new Error('offline'))

    const todayDate = new Date().toISOString().slice(0, 10)
    const history: AlertHistory[] = [
      { ...makeHistory('h1', 'AAPL'), fired_at: `${todayDate}T09:00:00Z` },
      { ...makeHistory('h2', 'AAPL'), fired_at: `${todayDate}T15:30:00Z` },
    ]
    useAlertStore.setState({ history })

    await useAlertStore.getState().fetchAlertStats()

    const { daily_counts } = useAlertStore.getState().alertStats!
    const todayBucket = daily_counts.find((d) => d.date === todayDate)
    expect(todayBucket).toBeDefined()
    expect(todayBucket!.count).toBe(2)
  })

  it('produces zero-count buckets for days with no events', async () => {
    vi.mocked(api.fetchAlertStats).mockRejectedValue(new Error('offline'))
    useAlertStore.setState({ history: [] })

    await useAlertStore.getState().fetchAlertStats()

    const { daily_counts } = useAlertStore.getState().alertStats!
    expect(daily_counts.every((d) => d.count === 0)).toBe(true)
  })

  it('does not throw and still sets alertStats when history is empty', async () => {
    vi.mocked(api.fetchAlertStats).mockRejectedValue(new Error('offline'))
    useAlertStore.setState({ history: [] })

    await expect(useAlertStore.getState().fetchAlertStats()).resolves.toBeUndefined()

    const stats = useAlertStore.getState().alertStats!
    expect(stats.total_today).toBe(0)
    expect(stats.total_week).toBe(0)
    expect(stats.total_month).toBe(0)
    expect(stats.top_symbols).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// updateNotificationPrefs — persists to localStorage
// ---------------------------------------------------------------------------

describe('alertStore — updateNotificationPrefs', () => {
  let setItemSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
  })

  afterEach(() => {
    setItemSpy.mockRestore()
    localStorage.removeItem('alertNotificationPrefs')
    vi.clearAllMocks()
  })

  it('merges the partial update into the current prefs', () => {
    useAlertStore.getState().updateNotificationPrefs({ muted: true })
    expect(useAlertStore.getState().notificationPrefs.muted).toBe(true)
    // Other fields remain at their defaults
    expect(useAlertStore.getState().notificationPrefs.in_app).toBe(true)
  })

  it('calls localStorage.setItem with the correct key', () => {
    useAlertStore.getState().updateNotificationPrefs({ volume: 0.3 })
    expect(setItemSpy).toHaveBeenCalledWith(
      'alertNotificationPrefs',
      expect.any(String),
    )
  })

  it('persists the full merged object to localStorage', () => {
    useAlertStore.getState().updateNotificationPrefs({ volume: 0.3, muted: true })
    const raw = localStorage.getItem('alertNotificationPrefs')
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw!) as Record<string, unknown>
    expect(parsed.volume).toBe(0.3)
    expect(parsed.muted).toBe(true)
  })

  it('subsequent updates accumulate correctly', () => {
    useAlertStore.getState().updateNotificationPrefs({ sound: 'alarm' })
    useAlertStore.getState().updateNotificationPrefs({ volume: 0.1 })
    const prefs = useAlertStore.getState().notificationPrefs
    expect(prefs.sound).toBe('alarm')
    expect(prefs.volume).toBe(0.1)
  })

  it('does not affect alerts, history, or unreadCount', () => {
    useAlertStore.setState({ alerts: [makeAlert('a1')], unreadCount: 5 })
    useAlertStore.getState().updateNotificationPrefs({ browser_push: true })
    expect(useAlertStore.getState().alerts).toHaveLength(1)
    expect(useAlertStore.getState().unreadCount).toBe(5)
  })
})
