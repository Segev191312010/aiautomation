import { type FormEvent, useEffect, useMemo, useState } from 'react'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import AutopilotRuleLab from '@/components/rules/AutopilotRuleLab'
import { ConditionBuilder } from '@/components/rules/ConditionBuilder'
import {
  createRule,
  deleteRule,
  fetchAutopilotRules,
  fetchRules,
  toggleRule,
  updateRule,
} from '@/services/api'
import type {
  AssetType,
  Condition,
  HoldStyle,
  OrderAction,
  OrderType,
  Rule,
  RuleCreate,
  RuleStatus,
  RuleUniverse,
} from '@/types'

type ScopeMode = 'symbol' | 'universe'

interface RuleFormState {
  name: string
  scopeMode: ScopeMode
  symbol: string
  universe: '' | RuleUniverse
  enabled: boolean
  logic: 'AND' | 'OR'
  status: RuleStatus
  cooldownMinutes: string
  holdStyle: '' | HoldStyle
  actionType: OrderAction
  assetType: AssetType
  quantity: string
  orderType: OrderType
  limitPrice: string
  conditions: Condition[]
}

const DEFAULT_CONDITIONS: Condition[] = [
  {
    indicator: 'PRICE',
    params: {},
    operator: '>',
    value: 0,
  },
]

const EMPTY_FORM: RuleFormState = {
  name: '',
  scopeMode: 'symbol',
  symbol: '',
  universe: 'sp500',
  enabled: true,
  logic: 'AND',
  status: 'active',
  cooldownMinutes: '60',
  holdStyle: '',
  actionType: 'BUY',
  assetType: 'STK',
  quantity: '10',
  orderType: 'MKT',
  limitPrice: '',
  conditions: DEFAULT_CONDITIONS,
}

const RULE_STATUSES: RuleStatus[] = ['draft', 'paper', 'active', 'paused', 'retired']
const RULE_UNIVERSES: RuleUniverse[] = ['sp500', 'nasdaq100', 'etfs', 'all']

function toFormState(rule: Rule): RuleFormState {
  const hasUniverse = Boolean(rule.universe)
  return {
    name: rule.name,
    scopeMode: hasUniverse ? 'universe' : 'symbol',
    symbol: hasUniverse ? '' : rule.symbol,
    universe: hasUniverse ? (rule.universe ?? 'sp500') : 'sp500',
    enabled: rule.enabled,
    logic: rule.logic,
    status: rule.status ?? 'active',
    cooldownMinutes: String(rule.cooldown_minutes ?? 60),
    holdStyle: rule.hold_style ?? '',
    actionType: rule.action.type,
    assetType: rule.action.asset_type,
    quantity: String(rule.action.quantity),
    orderType: rule.action.order_type,
    limitPrice: rule.action.limit_price != null ? String(rule.action.limit_price) : '',
    conditions: rule.conditions ?? DEFAULT_CONDITIONS,
  }
}

function buildRulePayload(form: RuleFormState): RuleCreate {
  if (form.conditions.length === 0) {
    throw new Error('At least one condition is required.')
  }
  const parsedConditions = form.conditions

  const quantity = Number(form.quantity)
  if (!Number.isFinite(quantity) || quantity <= 0) {
    throw new Error('Quantity must be greater than zero.')
  }

  const cooldownMinutes = Number(form.cooldownMinutes)
  if (!Number.isFinite(cooldownMinutes) || cooldownMinutes < 0) {
    throw new Error('Cooldown must be zero or greater.')
  }

  const limitPrice = form.orderType === 'LMT' ? Number(form.limitPrice) : undefined
  if (form.orderType === 'LMT' && (!Number.isFinite(limitPrice) || Number(limitPrice) <= 0)) {
    throw new Error('Limit price must be greater than zero for limit orders.')
  }

  return {
    name: form.name.trim(),
    symbol: form.scopeMode === 'symbol' ? form.symbol.trim().toUpperCase() : '',
    universe: form.scopeMode === 'universe' ? (form.universe || null) : null,
    enabled: form.enabled,
    conditions: parsedConditions,
    logic: form.logic,
    action: {
      type: form.actionType,
      asset_type: form.assetType,
      quantity,
      order_type: form.orderType,
      limit_price: form.orderType === 'LMT' ? limitPrice : undefined,
    },
    cooldown_minutes: cooldownMinutes,
    status: form.status,
    ai_generated: false,
    created_by: 'human',
    hold_style: form.holdStyle || null,
  }
}

function describeRuleScope(rule: Rule): string {
  const symbol = rule.symbol?.trim()
  if (symbol) {
    return symbol
  }
  if (rule.universe) {
    return `Universe: ${rule.universe}`
  }
  return 'Scope unavailable'
}

function describeRuleAction(rule: Rule): string {
  const limit = rule.action.order_type === 'LMT' && rule.action.limit_price != null
    ? ` @ ${rule.action.limit_price}`
    : ''
  return `${rule.action.type} ${rule.action.quantity} ${rule.action.asset_type} ${rule.action.order_type}${limit}`
}

function formatRuleTimestamp(timestamp?: string | null): string {
  if (!timestamp) return 'Never'
  return new Date(timestamp).toLocaleString()
}

export default function RulesPage() {
  const [manualRules, setManualRules] = useState<Rule[]>([])
  const [aiRules, setAiRules] = useState<Rule[]>([])
  const [manualLoading, setManualLoading] = useState(false)
  const [aiLoading, setAiLoading] = useState(false)
  const [manualError, setManualError] = useState<string | null>(null)
  const [aiError, setAiError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [editingRuleId, setEditingRuleId] = useState<string | null>(null)
  const [form, setForm] = useState<RuleFormState>(EMPTY_FORM)

  const activeManualRules = useMemo(
    () => manualRules.filter((rule) => rule.enabled).length,
    [manualRules],
  )

  async function loadManualRules() {
    setManualLoading(true)
    setManualError(null)
    try {
      const rules = await fetchRules()
      setManualRules(rules.filter((rule) => !rule.ai_generated))
    } catch (error) {
      setManualError(error instanceof Error ? error.message : 'Failed to load standard rules')
    } finally {
      setManualLoading(false)
    }
  }

  async function loadAiRules() {
    setAiLoading(true)
    setAiError(null)
    try {
      setAiRules(await fetchAutopilotRules())
    } catch (error) {
      setAiError(error instanceof Error ? error.message : 'Failed to load autopilot rules')
    } finally {
      setAiLoading(false)
    }
  }

  async function refreshAll() {
    await Promise.allSettled([loadManualRules(), loadAiRules()])
  }

  useEffect(() => {
    void refreshAll()
  }, [])

  function resetForm() {
    setEditingRuleId(null)
    setForm(EMPTY_FORM)
    setFormError(null)
  }

  function startEdit(rule: Rule) {
    setEditingRuleId(rule.id)
    setForm(toFormState(rule))
    setFormError(null)
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    setSaving(true)
    try {
      const payload = buildRulePayload(form)
      if (!payload.name) {
        throw new Error('Rule name is required.')
      }
      if (form.scopeMode === 'symbol' && !payload.symbol.trim()) {
        throw new Error('Symbol is required for symbol-scoped rules.')
      }

      if (editingRuleId) {
        await updateRule(editingRuleId, payload)
      } else {
        await createRule(payload)
      }
      await loadManualRules()
      resetForm()
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'Failed to save rule')
    } finally {
      setSaving(false)
    }
  }

  async function handleToggle(rule: Rule) {
    setManualError(null)
    try {
      await toggleRule(rule.id)
      await loadManualRules()
    } catch (error) {
      setManualError(error instanceof Error ? error.message : `Failed to toggle ${rule.name}`)
    }
  }

  async function handleDelete(rule: Rule) {
    setManualError(null)
    try {
      await deleteRule(rule.id)
      if (editingRuleId === rule.id) {
        resetForm()
      }
      await loadManualRules()
    } catch (error) {
      setManualError(error instanceof Error ? error.message : `Failed to delete ${rule.name}`)
    }
  }

  return (
    <div className="space-y-6 pb-8">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--text-primary)]">Rules & Automation</h1>
          <p className="text-sm text-[var(--text-muted)] mt-1 max-w-3xl">
            Standard rules keep their direct CRUD workflow here. The AI rule lab remains visible below for Autopilot-generated rules,
            versions, and emergency overrides.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refreshAll()}
          className="rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-xs font-semibold text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
        >
          Refresh All
        </button>
      </div>

      <section className="grid gap-6 xl:grid-cols-[minmax(360px,420px),1fr]">
        <ErrorBoundary>
        <div className="rounded-2xl border border-[var(--border)] bg-white p-5 shadow-sm space-y-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--text-muted)]">
              Standard Rules
            </div>
            <h2 className="mt-2 text-lg font-semibold text-[var(--text-primary)]">
              {editingRuleId ? 'Edit Standard Rule' : 'Create Standard Rule'}
            </h2>
            <p className="mt-1 text-sm text-[var(--text-muted)]">
              This is the direct `/api/rules` control surface for normal rule CRUD.
            </p>
          </div>

          {formError && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {formError}
            </div>
          )}

          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="grid gap-4 md:grid-cols-2">
              <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                <span>Rule Name</span>
                <input
                  value={form.name}
                  onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                  className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                  placeholder="Momentum breakout"
                />
              </label>

              <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                <span>Scope</span>
                <select
                  value={form.scopeMode}
                  onChange={(event) => setForm((current) => ({
                    ...current,
                    scopeMode: event.target.value as ScopeMode,
                    symbol: event.target.value === 'symbol' ? current.symbol : '',
                  }))}
                  className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                >
                  <option value="symbol">Symbol</option>
                  <option value="universe">Universe</option>
                </select>
              </label>

              {form.scopeMode === 'symbol' ? (
                <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                  <span>Symbol</span>
                  <input
                    value={form.symbol}
                    onChange={(event) => setForm((current) => ({ ...current, symbol: event.target.value.toUpperCase() }))}
                    className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                    placeholder="AAPL"
                  />
                </label>
              ) : (
                <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                  <span>Universe</span>
                  <select
                    value={form.universe}
                    onChange={(event) => setForm((current) => ({ ...current, universe: event.target.value as RuleUniverse }))}
                    className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                  >
                    {RULE_UNIVERSES.map((universe) => (
                      <option key={universe} value={universe}>{universe}</option>
                    ))}
                  </select>
                </label>
              )}

              <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                <span>Status</span>
                <select
                  value={form.status}
                  onChange={(event) => setForm((current) => ({ ...current, status: event.target.value as RuleStatus }))}
                  className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                >
                  {RULE_STATUSES.map((status) => (
                    <option key={status} value={status}>{status}</option>
                  ))}
                </select>
              </label>

              <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                <span>Logic</span>
                <select
                  value={form.logic}
                  onChange={(event) => setForm((current) => ({ ...current, logic: event.target.value as 'AND' | 'OR' }))}
                  className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                >
                  <option value="AND">AND</option>
                  <option value="OR">OR</option>
                </select>
              </label>

              <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                <span>Cooldown Minutes</span>
                <input
                  type="number"
                  min="0"
                  value={form.cooldownMinutes}
                  onChange={(event) => setForm((current) => ({ ...current, cooldownMinutes: event.target.value }))}
                  className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                />
              </label>

              <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                <span>Hold Style</span>
                <select
                  value={form.holdStyle}
                  onChange={(event) => setForm((current) => ({ ...current, holdStyle: event.target.value as '' | HoldStyle }))}
                  className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                >
                  <option value="">Unspecified</option>
                  <option value="intraday">intraday</option>
                  <option value="swing">swing</option>
                </select>
              </label>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                <span>Action</span>
                <select
                  value={form.actionType}
                  onChange={(event) => setForm((current) => ({ ...current, actionType: event.target.value as OrderAction }))}
                  className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                >
                  <option value="BUY">BUY</option>
                  <option value="SELL">SELL</option>
                </select>
              </label>

              <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                <span>Asset Type</span>
                <select
                  value={form.assetType}
                  onChange={(event) => setForm((current) => ({ ...current, assetType: event.target.value as AssetType }))}
                  className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                >
                  <option value="STK">STK</option>
                  <option value="OPT">OPT</option>
                  <option value="FUT">FUT</option>
                </select>
              </label>

              <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                <span>Quantity</span>
                <input
                  type="number"
                  min="1"
                  value={form.quantity}
                  onChange={(event) => setForm((current) => ({ ...current, quantity: event.target.value }))}
                  className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                />
              </label>

              <label className="space-y-1 text-sm text-[var(--text-secondary)]">
                <span>Order Type</span>
                <select
                  value={form.orderType}
                  onChange={(event) => setForm((current) => ({ ...current, orderType: event.target.value as OrderType }))}
                  className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                >
                  <option value="MKT">MKT</option>
                  <option value="LMT">LMT</option>
                </select>
              </label>

              {form.orderType === 'LMT' && (
                <label className="space-y-1 text-sm text-[var(--text-secondary)] md:col-span-2">
                  <span>Limit Price</span>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={form.limitPrice}
                    onChange={(event) => setForm((current) => ({ ...current, limitPrice: event.target.value }))}
                    className="w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                  />
                </label>
              )}
            </div>

            <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) => setForm((current) => ({ ...current, enabled: event.target.checked }))}
                className="h-4 w-4 rounded border-[var(--border)]"
              />
              Enabled
            </label>

            <ConditionBuilder
              conditions={form.conditions}
              onChange={(conditions) => setForm((current) => ({ ...current, conditions }))}
            />

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={saving}
                className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
              >
                {saving ? 'Saving...' : editingRuleId ? 'Save Changes' : 'Create Rule'}
              </button>
              <button
                type="button"
                onClick={resetForm}
                className="rounded-lg border border-[var(--border)] bg-white px-4 py-2 text-sm font-semibold text-[var(--text-secondary)]"
              >
                {editingRuleId ? 'Cancel Edit' : 'Reset Form'}
              </button>
            </div>
          </form>
        </div>
        </ErrorBoundary>

        <ErrorBoundary>
        <div className="rounded-2xl border border-[var(--border)] bg-white p-5 shadow-sm space-y-4">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--text-muted)]">
                Standard Rules Inventory
              </div>
              <h2 className="mt-2 text-lg font-semibold text-[var(--text-primary)]">Live CRUD rules</h2>
              <p className="mt-1 text-sm text-[var(--text-muted)]">
                {manualRules.length} standard rules loaded, {activeManualRules} currently enabled.
              </p>
            </div>
            <button
              type="button"
              onClick={() => void loadManualRules()}
              className="rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-xs font-semibold text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
            >
              Refresh Standard Rules
            </button>
          </div>

          {manualError && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {manualError}
            </div>
          )}

          {manualLoading && !manualRules.length ? (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-muted)] px-4 py-6 text-sm text-[var(--text-muted)]">
              Loading standard rules...
            </div>
          ) : manualRules.length === 0 ? (
            <div className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-muted)] px-4 py-6 text-sm text-[var(--text-muted)]">
              No standard rules found. Create one from the form to restore direct rule automation.
            </div>
          ) : (
            <div className="space-y-3">
              {manualRules.map((rule) => (
                <article key={rule.id} className="rounded-xl border border-[var(--border)] bg-[var(--bg-muted)] p-4 space-y-3">
                  <div className="flex items-start justify-between gap-3 flex-wrap">
                    <div>
                      <h3 className="text-base font-semibold text-[var(--text-primary)]">{rule.name}</h3>
                      <p className="mt-1 text-sm text-[var(--text-muted)]">
                        {describeRuleScope(rule)} · {describeRuleAction(rule)}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 flex-wrap text-xs font-semibold">
                      <span className="rounded-full bg-white px-2.5 py-1 text-[var(--text-secondary)] border border-[var(--border)]">
                        {rule.status ?? 'active'}
                      </span>
                      <span className={`rounded-full px-2.5 py-1 border ${rule.enabled ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-zinc-200 bg-zinc-50 text-zinc-600'}`}>
                        {rule.enabled ? 'enabled' : 'disabled'}
                      </span>
                    </div>
                  </div>

                  <div className="grid gap-3 text-sm text-[var(--text-secondary)] md:grid-cols-2">
                    <div>
                      <div className="font-semibold text-[var(--text-primary)]">Conditions</div>
                      <div>{rule.conditions.length} rule condition{rule.conditions.length === 1 ? '' : 's'}</div>
                    </div>
                    <div>
                      <div className="font-semibold text-[var(--text-primary)]">Cooldown</div>
                      <div>{rule.cooldown_minutes} minutes</div>
                    </div>
                    <div>
                      <div className="font-semibold text-[var(--text-primary)]">Last Triggered</div>
                      <div>{formatRuleTimestamp(rule.last_triggered)}</div>
                    </div>
                    <div>
                      <div className="font-semibold text-[var(--text-primary)]">Updated</div>
                      <div>{formatRuleTimestamp(rule.updated_at)}</div>
                    </div>
                  </div>

                  <details className="rounded-lg border border-[var(--border)] bg-white px-3 py-2">
                    <summary className="cursor-pointer text-sm font-semibold text-[var(--text-primary)]">Condition JSON</summary>
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs text-[var(--text-secondary)]">
                      {JSON.stringify(rule.conditions, null, 2)}
                    </pre>
                  </details>

                  <div className="flex items-center gap-2 flex-wrap">
                    <button
                      type="button"
                      onClick={() => startEdit(rule)}
                      className="rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-xs font-semibold text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleToggle(rule)}
                      className="rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-xs font-semibold text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
                    >
                      {rule.enabled ? 'Disable' : 'Enable'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDelete(rule)}
                      className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-100"
                    >
                      Delete
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
        </ErrorBoundary>
      </section>

      <ErrorBoundary>
      <section className="space-y-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h2 className="text-2xl font-semibold text-[var(--text-primary)]">AI Rules</h2>
            <p className="text-sm text-[var(--text-muted)] mt-1">
              Read-only view of rules generated and managed by Autopilot, with version history and emergency overrides.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadAiRules()}
            className="rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-xs font-semibold text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
          >
            Refresh AI Rules
          </button>
        </div>

        {aiError && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {aiError}
          </div>
        )}

        {aiLoading && !aiRules.length ? (
          <div className="rounded-2xl border border-[var(--border)] bg-white px-5 py-8 text-sm text-[var(--text-muted)]">
            Loading AI rule inventory...
          </div>
        ) : (
          <AutopilotRuleLab rules={aiRules} onRefresh={loadAiRules} />
        )}
      </section>
      </ErrorBoundary>
    </div>
  )
}


