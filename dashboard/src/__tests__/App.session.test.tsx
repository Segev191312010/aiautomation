import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

import { useSessionStore } from '@/store/sessionStore'


const apiMocks = vi.hoisted(() => ({
  bootstrapSession: vi.fn(),
  fetchStatus: vi.fn(),
}))

vi.mock('@/services/api', () => ({
  bootstrapSession: apiMocks.bootstrapSession,
  fetchStatus: apiMocks.fetchStatus,
}))
vi.mock('@/components/layout/Layout', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/pages/Dashboard', () => ({
  default: () => <div>Protected dashboard</div>,
}))

import App from '@/App'


describe('App session bootstrap lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useSessionStore.setState(useSessionStore.getInitialState(), true)
    localStorage.clear()
    window.history.replaceState({}, '', '/')
    apiMocks.fetchStatus.mockResolvedValue({
      ibkr_connected: false,
      is_paper: true,
      sim_mode: true,
      bot_running: false,
    })
  })

  it('bootstraps once and mounts the guarded workspace on success', async () => {
    apiMocks.bootstrapSession.mockResolvedValue({
      access_token: 'app-memory-token',
      token_type: 'bearer',
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      expires_in_seconds: 60,
    })

    render(<App />)

    expect(screen.queryByText('Protected dashboard')).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Protected dashboard')).toBeInTheDocument())
    expect(apiMocks.bootstrapSession).toHaveBeenCalledTimes(1)
    expect(useSessionStore.getState()).toMatchObject({
      token: 'app-memory-token',
      status: 'authenticated',
    })
    expect(localStorage.getItem('auth_token')).toBeNull()
  })

  it('shows the explicit failure boundary instead of the workspace', async () => {
    apiMocks.bootstrapSession.mockRejectedValue(new Error('backend unavailable'))

    render(<App />)

    await waitFor(() => {
      expect(screen.getByRole('alert', { name: /Session bootstrap failed/i })).toBeInTheDocument()
    })
    expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.queryByText('Protected dashboard')).not.toBeInTheDocument()
    expect(useSessionStore.getState().status).toBe('failed')
  })
})
