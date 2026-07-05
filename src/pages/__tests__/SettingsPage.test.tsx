import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('@/services/api', () => ({
  fetchStatus: vi.fn().mockResolvedValue({
    ibkr_connected: false, ibkr_host: '127.0.0.1', ibkr_port: 7497,
    is_paper: true, sim_mode: true, mock_mode: false,
    bot_running: false, bot_interval_seconds: 900,
  }),
  fetchAutopilotConfig: vi.fn().mockResolvedValue({
    autopilot_mode: 'PAPER', emergency_stop: false, daily_loss_locked: false, daily_loss_limit_pct: 2,
  }),
  connectIBKR: vi.fn(),
  disconnectIBKR: vi.fn(),
  resetSimAccount: vi.fn(),
  setAutopilotMode: vi.fn(),
  resetDailyLossLock: vi.fn(),
}))

import SettingsPage from '@/pages/SettingsPage'

describe('SettingsPage', () => {
  it('renders system status and autopilot configuration sections', async () => {
    render(<SettingsPage />)
    expect(await screen.findByText('System Status')).toBeInTheDocument()
    expect(await screen.findByText('Autopilot Configuration')).toBeInTheDocument()
    // status row populated from the mocked backend
    expect(await screen.findByText('127.0.0.1')).toBeInTheDocument()
  })
})
