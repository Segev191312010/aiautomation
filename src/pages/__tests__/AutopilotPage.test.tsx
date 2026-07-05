import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

// Mock the API layer so the page renders without a backend.
vi.mock('@/services/api', () => ({
  fetchAutopilotStatus: vi.fn().mockResolvedValue({
    mode: 'PAPER', autonomy_active: true, shadow_mode: true, emergency_stop: false,
    daily_loss_locked: false, daily_loss_limit_pct: 2, broker_connected: false,
    open_positions_count: 0, active_rules_count: 3, direct_ai_open_trades_count: 0,
    last_action_at: null, changes_today: 0, next_optimization_at: null,
    last_optimization_at: null, daily_budget_remaining: 10, optimizer_running: false, bot_health: null,
  }),
  fetchAutopilotRules: vi.fn().mockResolvedValue([]),
  fetchAutopilotPerformance: vi.fn().mockResolvedValue({
    window_days: 30, total_trades: 0, hit_rate: null, realized_pnl: 0,
    unrealized_pnl: 0, total_cost: 0, roi: null, by_source: [],
  }),
  fetchAutopilotFeed: vi.fn().mockResolvedValue({ entries: [], total: 0, offset: 0, limit: 25 }),
  setAutopilotMode: vi.fn(),
  activateKillSwitch: vi.fn(),
  resetKillSwitch: vi.fn(),
  resetDailyLossLock: vi.fn(),
  promoteRule: vi.fn(),
  pauseRule: vi.fn(),
  retireRule: vi.fn(),
}))

import AutopilotPage from '@/pages/AutopilotPage'

describe('AutopilotPage', () => {
  it('renders the control-panel sections', async () => {
    render(<AutopilotPage />)
    expect(await screen.findByText('Autopilot Mode & Authority')).toBeInTheDocument()
    expect(screen.getByText('Engine Status')).toBeInTheDocument()
  })
})
