import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('@/services/api', () => ({
  fetchRules: vi.fn().mockResolvedValue([]),
  createRule: vi.fn(),
  updateRule: vi.fn(),
  deleteRule: vi.fn(),
  toggleRule: vi.fn(),
}))

import RulesPage from '@/pages/RulesPage'

describe('RulesPage', () => {
  it('renders the rule engine and an empty state when there are no rules', async () => {
    render(<RulesPage />)
    expect(await screen.findByText('Rule Engine')).toBeInTheDocument()
    expect(await screen.findByText(/No rules yet/i)).toBeInTheDocument()
  })
})
