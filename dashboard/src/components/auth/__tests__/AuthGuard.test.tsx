import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

import { useSessionStore } from '@/store/sessionStore'
import AuthGuard from '../AuthGuard'


describe('AuthGuard session boundary', () => {
  beforeEach(() => {
    useSessionStore.setState(useSessionStore.getInitialState(), true)
    window.history.replaceState({}, '', '/')
  })

  it('does not mount the protected workspace before bootstrap completes', () => {
    useSessionStore.getState().beginBootstrap()

    render(
      <AuthGuard onRetry={vi.fn()}>
        <div>Protected desk</div>
      </AuthGuard>,
    )

    expect(screen.getByText(/Establishing secure session/i)).toBeInTheDocument()
    expect(screen.queryByText('Protected desk')).not.toBeInTheDocument()
  })

  it('mounts children only for an authenticated in-memory session', () => {
    useSessionStore.getState().setSession({
      accessToken: 'memory-only-token',
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    })

    render(
      <AuthGuard onRetry={vi.fn()}>
        <div>Protected desk</div>
      </AuthGuard>,
    )

    expect(screen.getByText('Protected desk')).toBeInTheDocument()
    expect(localStorage.getItem('auth_token')).toBeNull()
  })

  it('shows an explicit bootstrap failure and retries without fake login UI', async () => {
    const retry = vi.fn()
    useSessionStore.getState().failBootstrap('Session bootstrap failed: backend unavailable')

    render(
      <AuthGuard onRetry={retry}>
        <div>Protected desk</div>
      </AuthGuard>,
    )

    expect(screen.getByRole('alert', { name: /Session bootstrap failed/i })).toBeInTheDocument()
    expect(screen.queryByText('Protected desk')).not.toBeInTheDocument()
    screen.getByRole('button', { name: /Retry connection/i }).click()
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('expires an already elapsed session and unmounts children', async () => {
    useSessionStore.setState({
      token: 'expired-token',
      expiresAt: new Date(Date.now() - 1_000).toISOString(),
      status: 'authenticated',
      error: null,
    })

    render(
      <AuthGuard onRetry={vi.fn()}>
        <div>Protected desk</div>
      </AuthGuard>,
    )

    await waitFor(() => {
      expect(screen.getByRole('alert', { name: /Session expired/i })).toBeInTheDocument()
    })
    expect(screen.queryByText('Protected desk')).not.toBeInTheDocument()
    expect(window.location.pathname).toBe('/session-expired')
  })
})
