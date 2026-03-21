/**
 * Autopilot UI tests — AIStatusBar, AIActivityFeed, AutopilotRuleLab.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import React from 'react'

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('@/services/api', () => ({
  fetchAutopilotRuleVersions: vi.fn().mockResolvedValue([]),
  fetchAutopilotRuleValidations: vi.fn().mockResolvedValue([]),
  fetchAutopilotRulePromotionReadiness: vi.fn().mockResolvedValue({
    rule_id: 'r1',
    status: 'active',
    eligible: false,
    reasons: ['Not enough trades'],
    latest_validation: null,
  }),
  manualPauseAutopilotRule: vi.fn().mockResolvedValue({}),
  manualRetireAutopilotRule: vi.fn().mockResolvedValue({}),
}))

// ── Tests: AIStatusBar ───────────────────────────────────────────────────────

describe('AIStatusBar', () => {
  it('renders mode badge and kill switch button', async () => {
    const { default: AIStatusBar } = await import('../AIStatusBar')
    render(
      <AIStatusBar
        status={{
          mode: 'LIVE',
          autonomy_active: true,
          shadow_mode: false,
          emergency_stop: false,
          daily_loss_locked: false,
          daily_loss_limit_pct: 4.0,
          broker_connected: true,
          open_positions_count: 3,
          active_rules_count: 25,
          direct_ai_open_trades_count: 1,
          last_action_at: null,
          changes_today: 5,
          next_optimization_at: null,
          daily_budget_remaining: 5,
          last_optimization_at: null,
          optimizer_running: false,
        }}
        onKillToggle={vi.fn()}
      />,
    )
    expect(screen.getByText('LIVE')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /kill/i })).toBeInTheDocument()
  })

  it('shows positions and rules count', async () => {
    const { default: AIStatusBar } = await import('../AIStatusBar')
    render(
      <AIStatusBar
        status={{
          mode: 'LIVE',
          autonomy_active: true,
          shadow_mode: false,
          emergency_stop: false,
          daily_loss_locked: false,
          daily_loss_limit_pct: 4.0,
          broker_connected: true,
          open_positions_count: 3,
          active_rules_count: 25,
          direct_ai_open_trades_count: 1,
          last_action_at: null,
          changes_today: 5,
          next_optimization_at: null,
          daily_budget_remaining: 5,
          last_optimization_at: null,
          optimizer_running: false,
        }}
      />,
    )
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('25')).toBeInTheDocument()
  })

  it('shows loading state when status is null', async () => {
    const { default: AIStatusBar } = await import('../AIStatusBar')
    render(<AIStatusBar status={null} />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('shows resume button when emergency stop is active', async () => {
    const { default: AIStatusBar } = await import('../AIStatusBar')
    render(
      <AIStatusBar
        status={{
          mode: 'LIVE',
          autonomy_active: true,
          shadow_mode: false,
          emergency_stop: true,
          daily_loss_locked: false,
          daily_loss_limit_pct: 4.0,
          broker_connected: true,
          open_positions_count: 0,
          active_rules_count: 0,
          direct_ai_open_trades_count: 0,
          last_action_at: null,
          changes_today: 0,
          next_optimization_at: null,
          daily_budget_remaining: 5,
          last_optimization_at: null,
          optimizer_running: false,
        }}
        onKillToggle={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /resume/i })).toBeInTheDocument()
    expect(screen.getByText('ACTIVE')).toBeInTheDocument()
  })
})

// ── Tests: AIActivityFeed ────────────────────────────────────────────────────

describe('AIActivityFeed', () => {
  it('renders feed entries with descriptions', async () => {
    const { default: AIActivityFeed } = await import('../AIActivityFeed')
    render(
      <AIActivityFeed
        entries={[
          {
            id: 1,
            timestamp: '2026-03-21T14:00:00Z',
            action_type: 'rule_create',
            category: 'rule_lab',
            description: 'Created AI rule: RSI Oversold AAPL',
            status: 'applied',
            confidence: 0.8,
          },
          {
            id: 2,
            timestamp: '2026-03-21T13:55:00Z',
            action_type: 'direct_trade_buy',
            category: 'direct_ai',
            description: 'Live BUY 5 TSLA',
            status: 'applied',
            confidence: 0.75,
          },
        ]}
        onRevert={vi.fn()}
      />,
    )
    expect(screen.getByText(/RSI Oversold AAPL/)).toBeInTheDocument()
    expect(screen.getByText(/BUY 5 TSLA/)).toBeInTheDocument()
  })

  it('shows empty state when no entries', async () => {
    const { default: AIActivityFeed } = await import('../AIActivityFeed')
    render(<AIActivityFeed entries={[]} onRevert={vi.fn()} />)
    expect(screen.getByText(/no ai activity/i)).toBeInTheDocument()
  })

  it('shows revert button for applied entries', async () => {
    const { default: AIActivityFeed } = await import('../AIActivityFeed')
    render(
      <AIActivityFeed
        entries={[
          {
            id: 1,
            timestamp: '2026-03-21T14:00:00Z',
            action_type: 'rule_create',
            category: 'rule_lab',
            description: 'Test entry',
            status: 'applied',
            confidence: 0.9,
          },
        ]}
        onRevert={vi.fn()}
      />,
    )
    expect(screen.getByText('Revert')).toBeInTheDocument()
  })
})

// ── Tests: AutopilotRuleLab ──────────────────────────────────────────────────

describe('AutopilotRuleLab', () => {
  const mockRules = [
    {
      id: 'r1',
      name: 'AI: RSI Bounce',
      symbol: 'AAPL',
      enabled: true,
      status: 'active',
      ai_generated: true,
      ai_reason: 'RSI oversold bounce detected',
      thesis: null,
      hold_style: 'intraday',
      version: 2,
      created_by: 'ai',
      conditions: [{ indicator: 'RSI', comparator: 'LESS_THAN', value: 30, timeframe: '1d' }],
      logic: 'AND' as const,
      action: { type: 'BUY' as const, asset_type: 'STK' as const, quantity: 1, order_type: 'MKT' as const },
      cooldown_minutes: 60,
    },
    {
      id: 'r2',
      name: 'Manual Momentum',
      symbol: 'TSLA',
      enabled: true,
      status: 'active',
      ai_generated: false,
      conditions: [{ indicator: 'PRICE', comparator: 'GREATER_THAN', value: 200, timeframe: '1d' }],
      logic: 'AND' as const,
      action: { type: 'BUY' as const, asset_type: 'STK' as const, quantity: 1, order_type: 'MKT' as const },
      cooldown_minutes: 60,
    },
  ]

  it('renders rules table with rule names', async () => {
    const { default: AutopilotRuleLab } = await import('../../rules/AutopilotRuleLab')
    render(<AutopilotRuleLab rules={mockRules as any} onRefresh={vi.fn()} />)
    // First rule appears in table + version history panel (auto-selected), so use getAllByText
    expect(screen.getAllByText('AI: RSI Bounce').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Manual Momentum')).toBeInTheDocument()
  })

  it('shows status badges', async () => {
    const { default: AutopilotRuleLab } = await import('../../rules/AutopilotRuleLab')
    render(<AutopilotRuleLab rules={mockRules as any} onRefresh={vi.fn()} />)
    const activeBadges = screen.getAllByText('active')
    expect(activeBadges.length).toBeGreaterThanOrEqual(2)
  })

  it('shows emergency Pause and Retire buttons (not Create/Edit)', async () => {
    const { default: AutopilotRuleLab } = await import('../../rules/AutopilotRuleLab')
    render(<AutopilotRuleLab rules={mockRules as any} onRefresh={vi.fn()} />)
    // Has emergency actions
    const pauseButtons = screen.getAllByText('Pause')
    expect(pauseButtons.length).toBeGreaterThan(0)
    const retireButtons = screen.getAllByText('Retire')
    expect(retireButtons.length).toBeGreaterThan(0)
    // Does NOT have create/edit buttons
    expect(screen.queryByText(/Create Rule/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Edit Rule/i)).not.toBeInTheDocument()
  })

  it('shows empty state when no rules', async () => {
    const { default: AutopilotRuleLab } = await import('../../rules/AutopilotRuleLab')
    render(<AutopilotRuleLab rules={[]} onRefresh={vi.fn()} />)
    expect(screen.getByText(/no ai-managed rules/i)).toBeInTheDocument()
  })
})
