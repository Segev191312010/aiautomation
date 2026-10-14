import { render, screen, within } from '@testing-library/react'

import type { Slice } from '../hooks/useLiveData'
import {
  MANAGER_PLAN_FIXTURE,
  MANAGER_PLANS_FIXTURE,
  MANAGER_SPEND_FIXTURE,
  MANAGER_STATUS_FIXTURE,
  MANAGER_VERDICTS_FIXTURE,
} from '../testing/fixtures'
import type { ManagerCurrentPlanView, ManagerSpend, ManagerStatusView, ManagerUnavailable } from '../types'
import {
  AutopilotPanel,
  describeNeutralReason,
  describePlanOutcome,
  describeSpend,
  describeStep,
  spendTone,
} from './AutopilotPanel'

function slice<T>(data: T, error: string | null = null): Slice<T> {
  return { data, error, receivedAt: 1 }
}

/** Nothing received yet (the type comes from the prop it is passed to). */
function empty<T>(error: string | null = null): Slice<T> {
  return { data: null, error, receivedAt: null }
}

const FULL = {
  status: slice<ManagerStatusView>(MANAGER_STATUS_FIXTURE),
  plan: slice<ManagerCurrentPlanView>(MANAGER_PLAN_FIXTURE),
  plans: slice(MANAGER_PLANS_FIXTURE),
  verdicts: slice(MANAGER_VERDICTS_FIXTURE),
  spend: slice(MANAGER_SPEND_FIXTURE),
}

describe('AutopilotPanel', () => {
  it('shows the switch, the plan in force with its steps and focus notes, the history, the verdicts and the spend', () => {
    render(<AutopilotPanel {...FULL} />)

    const sw = screen.getByLabelText('Autopilot switch')
    expect(within(sw).getByText('Autopilot ON').className).toContain('switch-on')
    expect(within(sw).getByText('since 09:02 ET · via Telegram · trial week')).toBeTruthy()
    expect(within(sw).getByText(/follows the plan \(latched 09:55 ET\)/)).toBeTruthy()

    const counts = screen.getByLabelText('Autopilot counts')
    expect(within(counts).getByText('Plans').nextSibling?.textContent).toBe('2/3')
    expect(within(counts).getByText('Day universe').nextSibling?.textContent).toBe('2')
    expect(within(counts).getByText('Schema').nextSibling?.textContent).toBe('v16')

    const current = screen.getByLabelText('Plan in force')
    expect(within(current).getByText('Range')).toBeTruthy()
    expect(within(current).getByText('09:46 ET #2')).toBeTruthy()
    expect(within(current).getByText('valid until 11:05 ET')).toBeTruthy()
    const steps = within(current).getByLabelText('Strategy steps')
    const [momo, orb] = within(steps).getAllByRole('listitem')
    expect(within(momo).getByText('MOMO')).toBeTruthy()
    expect(within(momo).getByText('on ×0.5').className).toContain('badge-take')
    expect(within(momo).getByText('choppy tape')).toBeTruthy()
    expect(within(orb).getByText('on')).toBeTruthy()
    const focus = within(current).getByLabelText('Focus list')
    const entries = within(focus).getAllByRole('listitem')
    expect(entries.map((entry) => entry.querySelector('.symbol')?.textContent)).toEqual(['BF B', 'AAPL', 'con 777'])
    expect(within(entries[0]).getByText('momo, orb')).toBeTruthy()
    expect(within(entries[0]).getByText('holding VWAP')).toBeTruthy()
    expect(within(entries[2]).getByText('no symbol known')).toBeTruthy()

    const history = screen.getByLabelText('Plan history')
    const rows = within(history).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(within(rows[0]).getByText('09:46 #2')).toBeTruthy()
    expect(within(rows[0]).getByText('in force')).toBeTruthy()
    expect(within(rows[0]).getByText('focus 3 · 7.2 s · $0.21 · until 11:05 ET')).toBeTruthy()
    expect(within(rows[1]).getByText('AI timed out').className).toContain('badge-ai')
    expect(within(rows[1]).getByText('no focus · 90.0 s · $0.00')).toBeTruthy()
    expect(within(rows[2]).getByText('focus 1 · dropped 1 · 8.4 s · $0.41 · until 10:35 ET')).toBeTruthy()
    expect(within(rows[2]).queryByText('in force')).toBeNull()

    const verdicts = screen.getByLabelText('Verdicts')
    const items = within(verdicts).getAllByRole('listitem')
    expect(items.map((item) => item.querySelector('.symbol')?.textContent)).toEqual(['setup pending', 'AAPL', 'BF B'])
    expect(within(items[0]).getByText('neutral')).toBeTruthy()
    expect(within(items[1]).getByText('SKIP').className).toContain('badge-ai')
    expect(within(items[1]).getByText('Extended from VWAP')).toBeTruthy()
    expect(within(items[2]).getByText('TAKE ×0.5').className).toContain('badge-take')
    expect(within(items[2]).getByText('Tight stop; half size')).toBeTruthy()
    expect(within(items[2]).getByText('entry 42.1 · stop 41.6 · target 43.1')).toBeTruthy()
    expect(within(items[2]).getByText('09:58:00')).toBeTruthy()

    const meter = screen.getByLabelText('Plan spend today')
    expect(within(meter).getByText('$0.62 of $5.00 · 12%')).toBeTruthy()
    expect(within(meter).getByRole('meter').getAttribute('aria-valuenow')).toBe('12.4')
    expect(within(meter).getByText('2 calls · $4.38 left')).toBeTruthy()
    expect(meter.className).toContain('spend-ok')

    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('is a monitor only: no buttons, no forms, no links to a control', () => {
    const { container } = render(<AutopilotPanel {...FULL} />)
    expect(container.querySelectorAll('button, form, input, select, textarea, a')).toHaveLength(0)
    expect(screen.getByText(/switched from Telegram, never from here/)).toBeTruthy()
  })

  it('raises the competing-login banner only while the alert is fresh', () => {
    const active: ManagerStatusView = {
      ...MANAGER_STATUS_FIXTURE,
      competing_login: { active: true, last_alert_at: '2026-10-13T14:01:00.000Z' },
    }
    const { rerender } = render(<AutopilotPanel {...FULL} status={slice(active)} />)
    expect(screen.getByRole('alert').textContent).toContain('IBKR login active elsewhere')
    expect(screen.getByRole('alert').textContent).toContain('10:01 ET')

    const stale: ManagerStatusView = {
      ...MANAGER_STATUS_FIXTURE,
      competing_login: { active: false, last_alert_at: '2026-10-13T13:20:00.000Z' },
    }
    rerender(<AutopilotPanel {...FULL} status={slice(stale)} />)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows the switch off, a neutral day and no plan in force with the last attempt', () => {
    const off: ManagerStatusView = {
      ...MANAGER_STATUS_FIXTURE,
      switch: { state: 'off', since: null, source: null, reason: null },
      latch: { family: 'momo', mode: 'v2', managed: false, latched_at: '2026-10-13T13:55:00.000Z' },
      plans: { attempts: 3, valid: 2, in_force: null },
      spend: { ...MANAGER_STATUS_FIXTURE.spend, committed_usd: '4.2', used_pct: 84, remaining_usd: '0.8' },
    }
    const expired: ManagerCurrentPlanView = {
      available: true,
      session_date: '2026-10-13',
      in_force: false,
      plan: null,
      last_attempt: MANAGER_PLANS_FIXTURE.plans[0],
      neutral_reason: 'plan_expired',
    }
    render(<AutopilotPanel {...FULL} status={slice(off)} plan={slice(expired)} spend={empty()} />)

    const sw = screen.getByLabelText('Autopilot switch')
    expect(within(sw).getByText('Autopilot OFF').className).toContain('switch-off')
    expect(within(sw).getByText('never switched')).toBeTruthy()
    expect(within(sw).getByText(/runs neutral, exactly as MOMO_HOD_V2/)).toBeTruthy()
    const current = screen.getByLabelText('Plan in force')
    expect(within(current).getByText('No plan in force: managed strategies run neutral (the last plan expired).')).toBeTruthy()
    expect(within(current).getByText('Last attempt 09:46 ET #2: Valid · was valid until 11:05 ET')).toBeTruthy()
    // The status carries the spend when the spend route has not answered yet.
    const meter = screen.getByLabelText('Plan spend today')
    expect(within(meter).getByText('$4.20 of $5.00 · 84%')).toBeTruthy()
    expect(meter.className).toContain('spend-warn')
  })

  it('explains loading, a pending migration and failing polls without hiding stale data', () => {
    const { rerender } = render(
      <AutopilotPanel status={empty()} plan={empty()} plans={empty()} verdicts={empty()} spend={empty()} />,
    )
    expect(screen.getByText('Loading autopilot…')).toBeTruthy()
    expect(screen.queryByLabelText('Autopilot switch')).toBeNull()

    const pending: ManagerUnavailable = {
      available: false,
      reason: 'schema_migration_pending',
      detail: 'schema v15, this code needs v16 (pending: 16)',
    }
    rerender(
      <AutopilotPanel status={slice(pending)} plan={slice(pending)} plans={slice(pending)} verdicts={slice(pending)} spend={slice(pending)} />,
    )
    expect(
      screen.getByText('Autopilot data unavailable: database migration pending (schema v15, this code needs v16 (pending: 16))'),
    ).toBeTruthy()
    expect(screen.queryByLabelText('Autopilot switch')).toBeNull()

    rerender(<AutopilotPanel {...FULL} verdicts={slice(MANAGER_VERDICTS_FIXTURE, 'HTTP 503')} spend={empty('HTTP 503')} />)
    expect(screen.getByRole('alert').textContent).toBe('Autopilot: HTTP 503')
    expect(screen.getByLabelText('Verdicts')).toBeTruthy()
    expect(screen.getByLabelText('Autopilot switch')).toBeTruthy()
  })

  it('names outcomes, steps, regimes and spend the way the owner reads them', () => {
    expect(describePlanOutcome('valid')).toEqual({ label: 'Valid', tone: 'take' })
    expect(describePlanOutcome('budget_exhausted')).toEqual({ label: 'Budget spent', tone: 'risk' })
    expect(describePlanOutcome('ai_order_field_echo').tone).toBe('risk')
    expect(describePlanOutcome('something_new')).toEqual({ label: 'something new', tone: 'warn' })
    expect(describeStep('1', 'day')).toEqual({ label: 'on', tone: 'take' })
    expect(describeStep('0.25', 'day')).toEqual({ label: 'on ×0.25', tone: 'take' })
    expect(describeStep('0', 'day')).toEqual({ label: 'OFF', tone: 'risk' })
    expect(describeStep('0', 'setup')).toEqual({ label: 'SKIP', tone: 'ai' })
    expect(describeStep('0.75', 'setup')).toEqual({ label: 'TAKE ×0.75', tone: 'take' })
    expect(describeStep('abc', 'setup')).toEqual({ label: 'abc', tone: 'warn' })
    expect(describeNeutralReason('plan_missing')).toBe('no plan written yet')
    expect(describeNeutralReason('switch_off')).toBe('the switch is off')
    expect(describeNeutralReason('odd_reason')).toBe('odd reason')
    const base: ManagerSpend = MANAGER_STATUS_FIXTURE.spend
    const spent: ManagerSpend = { ...base, committed_usd: '5', used_pct: 100, remaining_usd: '0', exhausted: true }
    expect(describeSpend(spent)).toBe('$5.00 of $5.00 · 100%')
    expect(spendTone(spent)).toBe('danger')
    expect(spendTone(base)).toBe('ok')
  })
})
