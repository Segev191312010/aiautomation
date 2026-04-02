import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useAlertStore } from '@/store/alertStore'
import type { Alert, AlertFiredEvent, AlertHistory } from '@/types'

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
