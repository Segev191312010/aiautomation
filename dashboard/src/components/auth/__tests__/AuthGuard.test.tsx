import React from 'react'
import { describe, expect, it, beforeEach, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'

const TOKEN_KEY = 'auth_token'

vi.mock('@/services/api', () => ({
  fetchAuthToken: vi.fn(async () => ({ access_token: 'fresh-token' })),
  setAuthToken: vi.fn((token: string | null) => {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  }),
  getAuthToken: vi.fn(() => localStorage.getItem(TOKEN_KEY)),
}))

vi.mock('../LoginPage', () => ({
  default: () => <div data-testid="login-page">Login required</div>,
}))

vi.mock('../RegisterPage', () => ({
  default: () => <div data-testid="register-page">Register</div>,
}))

import AuthGuard from '../AuthGuard'
import { fetchAuthToken, setAuthToken } from '@/services/api'

describe('AuthGuard token revocation flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('returns to login and clears storage when the API reports an unauthorized token', async () => {
    localStorage.setItem(TOKEN_KEY, 'stale-token')

    render(
      <AuthGuard>
        <div>Protected desk</div>
      </AuthGuard>,
    )

    await waitFor(() => {
      expect(screen.getByText('Protected desk')).toBeInTheDocument()
    })
    expect(fetchAuthToken).not.toHaveBeenCalled()
    expect(setAuthToken).toHaveBeenCalledWith('stale-token')

    act(() => {
      window.dispatchEvent(new Event('api:unauthorized'))
    })

    await waitFor(() => {
      expect(screen.getByTestId('login-page')).toBeInTheDocument()
    })
    expect(screen.queryByText('Protected desk')).not.toBeInTheDocument()
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull()
    expect(setAuthToken).toHaveBeenCalledWith(null)
  })

  it('bootstraps a fresh token when no stored token exists', async () => {
    render(
      <AuthGuard>
        <div>Protected desk</div>
      </AuthGuard>,
    )

    await waitFor(() => {
      expect(screen.getByText('Protected desk')).toBeInTheDocument()
    })
    expect(fetchAuthToken).toHaveBeenCalledTimes(1)
    expect(setAuthToken).toHaveBeenCalledWith('fresh-token')
    expect(localStorage.getItem(TOKEN_KEY)).toBe('fresh-token')
  })
})
