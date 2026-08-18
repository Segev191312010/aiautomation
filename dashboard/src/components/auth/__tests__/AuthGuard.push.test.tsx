import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const testState = vi.hoisted(() => ({
  fetchAuthToken: vi.fn(),
  fetchPushSubscriptionStatus: vi.fn(),
  setAuthToken: vi.fn(),
  getExistingPushSubscription: vi.fn(),
  unsubscribeLocalPush: vi.fn<() => Promise<void>>(),
  verifiedUnsubscribePush: vi.fn<() => Promise<void>>(),
  resetForAuthTransition: vi.fn(),
  pushGeneration: 0,
}))

vi.mock('@/services/api', () => ({
  fetchAuthToken: testState.fetchAuthToken,
  fetchPushSubscriptionStatus: testState.fetchPushSubscriptionStatus,
  setAuthToken: testState.setAuthToken,
}))

vi.mock('@/services/browserPush', () => ({
  beginPushSessionTransition: vi.fn(() => {
    testState.pushGeneration += 1
    return testState.pushGeneration
  }),
  getExistingPushSubscription: testState.getExistingPushSubscription,
  pushSessionIsCurrent: vi.fn((generation: number) => generation === testState.pushGeneration),
  runPushOperation: vi.fn(async (
    generation: number,
    operation: (context: {
      generation: number
      signal: AbortSignal
      assertCurrent: () => void
    }) => Promise<unknown>,
  ) => operation({
    generation,
    signal: new AbortController().signal,
    assertCurrent: () => {
      if (generation !== testState.pushGeneration) throw new Error('stale push session')
    },
  })),
  unsubscribeLocalPush: testState.unsubscribeLocalPush,
  verifiedUnsubscribePush: testState.verifiedUnsubscribePush,
}))

vi.mock('@/store', () => ({
  useAlertStore: {
    getState: () => ({ resetForAuthTransition: testState.resetForAuthTransition }),
  },
}))

vi.mock('@/components/ui/LoadingScreen', async () => {
  const React = await import('react')
  return { default: () => React.createElement('div', null, 'loading') }
})

vi.mock('@/components/auth/LoginPage', async () => {
  const React = await import('react')
  return {
    default: ({ onLogin }: { onLogin: (token: string) => void }) => React.createElement(
      'button',
      { onClick: () => onLogin('user-b-token') },
      'login as user b',
    ),
  }
})

vi.mock('@/components/auth/RegisterPage', async () => {
  const React = await import('react')
  return { default: () => React.createElement('div', null, 'register') }
})

import AuthGuard from '@/components/auth/AuthGuard'

const subscription = { endpoint: 'https://push.example.test/device-a' }

function renderGuard() {
  return render(
    <AuthGuard>
      <div>private application</div>
    </AuthGuard>,
  )
}

describe('AuthGuard browser push quarantine', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    testState.pushGeneration = 0
    testState.setAuthToken.mockImplementation((token: string | null, persist = true) => {
      if (token && persist) localStorage.setItem('auth_token', token)
      else localStorage.removeItem('auth_token')
    })
    testState.fetchAuthToken.mockRejectedValue(new Error('login required'))
    testState.getExistingPushSubscription.mockResolvedValue(null)
    testState.fetchPushSubscriptionStatus.mockResolvedValue({ registered: true })
    testState.verifiedUnsubscribePush.mockResolvedValue(undefined)
    testState.unsubscribeLocalPush.mockResolvedValue(undefined)
  })

  it('quarantines a foreign endpoint before mounting a stored session', async () => {
    let finishCleanup!: () => void
    localStorage.setItem('auth_token', 'user-b-token')
    testState.getExistingPushSubscription.mockResolvedValue(subscription)
    testState.fetchPushSubscriptionStatus.mockResolvedValue({ registered: false })
    testState.verifiedUnsubscribePush.mockImplementation(() => new Promise((resolve) => {
      finishCleanup = resolve
    }))

    renderGuard()

    await waitFor(() => expect(testState.fetchPushSubscriptionStatus).toHaveBeenCalledWith(
      subscription.endpoint,
      'user-b-token',
      expect.anything(),
    ))
    expect(screen.queryByText('private application')).not.toBeInTheDocument()
    expect(testState.setAuthToken).not.toHaveBeenCalledWith('user-b-token', true)

    await act(async () => finishCleanup())
    expect(await screen.findByText('private application')).toBeInTheDocument()
    expect(testState.setAuthToken).toHaveBeenCalledWith('user-b-token', true)
  })

  it('preserves an endpoint owned by the stored session', async () => {
    localStorage.setItem('auth_token', 'user-b-token')
    testState.getExistingPushSubscription.mockResolvedValue(subscription)

    renderGuard()

    expect(await screen.findByText('private application')).toBeInTheDocument()
    expect(testState.fetchPushSubscriptionStatus).toHaveBeenCalledWith(
      subscription.endpoint,
      'user-b-token',
      expect.anything(),
    )
    expect(testState.verifiedUnsubscribePush).not.toHaveBeenCalled()
  })

  it('gates a bootstrap token through foreign-endpoint cleanup', async () => {
    let finishCleanup!: () => void
    testState.fetchAuthToken.mockResolvedValue({ access_token: 'user-b-token' })
    testState.getExistingPushSubscription.mockResolvedValue(subscription)
    testState.fetchPushSubscriptionStatus.mockResolvedValue({ registered: false })
    testState.verifiedUnsubscribePush.mockImplementation(() => new Promise((resolve) => {
      finishCleanup = resolve
    }))

    renderGuard()

    await waitFor(() => expect(testState.fetchPushSubscriptionStatus).toHaveBeenCalledWith(
      subscription.endpoint,
      'user-b-token',
      expect.anything(),
    ))
    expect(testState.setAuthToken).not.toHaveBeenCalledWith('user-b-token', false)
    expect(screen.queryByText('private application')).not.toBeInTheDocument()

    await act(async () => finishCleanup())
    expect(await screen.findByText('private application')).toBeInTheDocument()
    expect(testState.setAuthToken).toHaveBeenCalledWith('user-b-token', false)
  })

  it('gates an interactive login through foreign-endpoint cleanup', async () => {
    renderGuard()
    expect(await screen.findByText('login as user b')).toBeInTheDocument()
    testState.getExistingPushSubscription.mockResolvedValue(subscription)
    testState.fetchPushSubscriptionStatus.mockResolvedValue({ registered: false })

    fireEvent.click(screen.getByText('login as user b'))

    expect(await screen.findByText('private application')).toBeInTheDocument()
    expect(testState.fetchPushSubscriptionStatus).toHaveBeenCalledWith(
      subscription.endpoint,
      'user-b-token',
      expect.anything(),
    )
    expect(testState.verifiedUnsubscribePush).toHaveBeenCalledWith(
      subscription,
      expect.objectContaining({ signal: expect.anything() }),
    )
    expect(testState.setAuthToken).toHaveBeenCalledWith('user-b-token', false)
  })

  it('fails closed when endpoint ownership cannot be verified and retries', async () => {
    localStorage.setItem('auth_token', 'user-b-token')
    testState.getExistingPushSubscription.mockResolvedValue(subscription)
    testState.fetchPushSubscriptionStatus.mockRejectedValueOnce(new Error('ownership unavailable'))

    renderGuard()

    expect(await screen.findByRole('alert')).toHaveTextContent('ownership unavailable')
    expect(screen.queryByText('private application')).not.toBeInTheDocument()
    expect(testState.setAuthToken).toHaveBeenCalledWith(null)

    fireEvent.click(screen.getByText('Retry securely'))
    expect(await screen.findByText('private application')).toBeInTheDocument()
  })

  it('does not mount a new account when unauthorized-session cleanup rejects', async () => {
    localStorage.setItem('auth_token', 'user-a-token')
    testState.unsubscribeLocalPush
      .mockRejectedValueOnce(new Error('browser refused cleanup'))
      .mockResolvedValueOnce(undefined)

    renderGuard()
    expect(await screen.findByText('private application')).toBeInTheDocument()

    act(() => window.dispatchEvent(new Event('api:unauthorized')))
    expect(await screen.findByRole('alert')).toHaveTextContent('browser refused cleanup')
    expect(screen.queryByText('private application')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('Retry securely'))
    expect(await screen.findByText('login as user b')).toBeInTheDocument()
    fireEvent.click(screen.getByText('login as user b'))
    expect(await screen.findByText('private application')).toBeInTheDocument()
    expect(testState.unsubscribeLocalPush).toHaveBeenCalledTimes(2)
  })
})
