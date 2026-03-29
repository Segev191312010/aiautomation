import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('@/components/rules/AutopilotRuleLab', () => ({
  default: ({ rules }: { rules: Array<{ id: string }> }) => (
    <div data-testid="autopilot-rule-lab">AI rules: {rules.length}</div>
  ),
}))

vi.mock('@/services/api', () => ({
  fetchRules: vi.fn(),
  fetchAutopilotRules: vi.fn(),
  createRule: vi.fn(),
  updateRule: vi.fn(),
  deleteRule: vi.fn(),
  toggleRule: vi.fn(),
}))

import RulesPage from '../RulesPage'
import * as api from '@/services/api'

const baseCondition = [{ indicator: 'PRICE', params: {}, operator: '>', value: 100 }]

const manualRule = {
  id: 'rule-1',
  name: 'Momentum Breakout',
  symbol: 'AAPL',
  universe: null,
  enabled: true,
  conditions: baseCondition,
  logic: 'AND' as const,
  action: {
    type: 'BUY' as const,
    asset_type: 'STK' as const,
    quantity: 10,
    order_type: 'MKT' as const,
  },
  cooldown_minutes: 60,
  last_triggered: null,
  status: 'active' as const,
  ai_generated: false,
  updated_at: '2026-03-27T10:00:00Z',
}

const aiRule = {
  ...manualRule,
  id: 'ai-rule-1',
  name: 'AI Momentum Draft',
  symbol: 'NVDA',
  ai_generated: true,
}

describe('RulesPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.fetchRules).mockResolvedValue([manualRule, aiRule] as never)
    vi.mocked(api.fetchAutopilotRules).mockResolvedValue([aiRule] as never)
    vi.mocked(api.createRule).mockResolvedValue({ ...manualRule, id: 'rule-2', name: 'Mean Reversion', symbol: 'MSFT' } as never)
    vi.mocked(api.updateRule).mockResolvedValue(manualRule as never)
    vi.mocked(api.deleteRule).mockResolvedValue({ deleted: true } as never)
    vi.mocked(api.toggleRule).mockResolvedValue({ id: manualRule.id, enabled: false } as never)
  })

  it('restores standard rules CRUD while keeping the AI rule lab visible', async () => {
    render(<RulesPage />)

    expect(await screen.findByText(/Rules & Automation/i)).toBeInTheDocument()
    expect(await screen.findByText('Momentum Breakout')).toBeInTheDocument()
    expect(screen.getByText(/Live CRUD rules/i)).toBeInTheDocument()
    expect(screen.getByTestId('autopilot-rule-lab')).toHaveTextContent('AI rules: 1')
    expect(api.fetchRules).toHaveBeenCalled()
    expect(api.fetchAutopilotRules).toHaveBeenCalled()
  })

  it('creates a standard rule through the restored CRUD form', async () => {
    vi.mocked(api.fetchRules)
      .mockResolvedValueOnce([manualRule, aiRule] as never)
      .mockResolvedValueOnce([
        manualRule,
        { ...manualRule, id: 'rule-2', name: 'Mean Reversion', symbol: 'MSFT' },
        aiRule,
      ] as never)

    render(<RulesPage />)
    await screen.findByText('Momentum Breakout')

    fireEvent.change(screen.getByLabelText('Rule Name'), { target: { value: 'Mean Reversion' } })
    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'msft' } })
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '25' } })
    fireEvent.click(screen.getByRole('button', { name: /create rule/i }))

    await waitFor(() => {
      expect(api.createRule).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Mean Reversion',
          symbol: 'MSFT',
          ai_generated: false,
          action: expect.objectContaining({ quantity: 25 }),
        }),
      )
    })

    expect(await screen.findByText('Mean Reversion')).toBeInTheDocument()
  })

  it('can toggle a standard rule without routing through the autopilot rules endpoint', async () => {
    render(<RulesPage />)
    await screen.findByText('Momentum Breakout')

    fireEvent.click(screen.getByRole('button', { name: 'Disable' }))

    await waitFor(() => {
      expect(api.toggleRule).toHaveBeenCalledWith('rule-1')
    })
  })
})
