import { beforeEach, describe, expect, it } from 'vitest'

import { useAccountStore } from '@/store/accountStore'
import { useBotStore } from '@/store/botStore'
import { useSessionStore } from '@/store/sessionStore'
import { useSimStore } from '@/store/simStore'


describe('sessionStore global reset', () => {
  beforeEach(() => {
    useSessionStore.setState(useSessionStore.getInitialState(), true)
  })

  it('keeps credentials only in memory', () => {
    useSessionStore.getState().setSession({
      accessToken: 'memory-only',
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    })

    expect(useSessionStore.getState()).toMatchObject({
      token: 'memory-only',
      status: 'authenticated',
    })
    expect(localStorage.getItem('auth_token')).toBeNull()
  })

  it('clears every representative domain store on session loss', () => {
    useAccountStore.setState({ loading: true })
    useBotStore.setState({ botRunning: true, ibkrConnected: true })
    useSimStore.setState({
      replayBars: [{ time: 1, open: 10, high: 12, low: 9, close: 11, volume: 100 }],
    })
    useSessionStore.getState().setSession({
      accessToken: 'soon-stale',
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    })

    useSessionStore.getState().reset('Unauthorized')

    expect(useAccountStore.getState().loading).toBe(false)
    expect(useBotStore.getState()).toMatchObject({ botRunning: false, ibkrConnected: false })
    expect(useSimStore.getState().replayBars).toEqual([])
    expect(useSessionStore.getState()).toMatchObject({
      token: null,
      expiresAt: null,
      status: 'expired',
      error: 'Unauthorized',
    })
  })

  it('rejects an already expired bootstrap response', () => {
    useSessionStore.getState().setSession({
      accessToken: 'expired',
      expiresAt: new Date(Date.now() - 1).toISOString(),
    })

    expect(useSessionStore.getState()).toMatchObject({
      token: null,
      status: 'failed',
    })
  })
})
