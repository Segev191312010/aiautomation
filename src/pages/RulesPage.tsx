/**
 * RulesPage — full rule-management UI.
 *
 * Sections:
 *  1. KPI header (total / enabled / disabled / armed-now)
 *  2. New / Edit rule form (collapsible)
 *  3. Rules table: name, symbol, action, #conditions, last-triggered,
 *     inline enable toggle, edit + delete controls.
 *
 * Mutations (create / update / toggle / delete) all refresh the list.
 */
import React, { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import KPICard from '@/components/tradebot/KPICard'
import RuleForm from '@/components/rules/RuleForm'
import { fetchRules, createRule, updateRule, deleteRule, toggleRule } from '@/services/api'
import type { Rule, RuleCreate } from '@/types'

// ── helpers ─────────────────────────────────────────────────────────────────────

function relTime(s: string | null | undefined): string {
  if (!s) return 'never'
  const d = new Date(s).getTime()
  if (Number.isNaN(d)) return 'never'
  const diff = Date.now() - d
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function fmtTime(s: string | null | undefined): string {
  if (!s) return ''
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString()
}

function condSummary(rule: Rule): string {
  return rule.conditions
    .map((c) => `${c.indicator} ${c.operator} ${c.value}`)
    .join(` ${rule.logic} `)
}

// ── page ────────────────────────────────────────────────────────────────────────

export default function RulesPage() {
  const [rules, setRules]     = useState<Rule[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [busy, setBusy]       = useState<string | null>(null)

  // form state: null = closed, 'new' = create, Rule = edit that rule
  const [editing, setEditing] = useState<Rule | 'new' | null>(null)

  const load = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true)
    try {
      const r = await fetchRules()
      setRules(r)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load rules')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(true)
    const t = setInterval(() => { void load(false) }, 20_000)
    return () => clearInterval(t)
  }, [load])

  const run = useCallback(async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setError(null)
    try {
      await fn()
      await load(false)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setBusy(null)
    }
  }, [load])

  const onToggle = (r: Rule) => run(`toggle:${r.id}`, () => toggleRule(r.id))

  const onDelete = (r: Rule) => {
    if (!window.confirm(`Delete rule "${r.name}"? This cannot be undone.`)) return
    void run(`delete:${r.id}`, () => deleteRule(r.id))
  }

  const onCreate = async (body: RuleCreate) => {
    await createRule(body)
    await load(false)
    setEditing(null)
  }

  const onUpdate = async (id: string, body: RuleCreate) => {
    await updateRule(id, body)
    await load(false)
    setEditing(null)
  }

  // ── KPIs ──────────────────────────────────────────────────────────────────────
  const total    = rules.length
  const enabled  = rules.filter((r) => r.enabled).length
  const disabled = total - enabled
  const buys     = rules.filter((r) => r.action.type === 'BUY').length

  return (
    <div className="flex flex-col gap-5">
      {/* ── error toast ────────────────────────────────────────────── */}
      {error && (
        <div className="bg-terminal-red/10 border border-terminal-red/40 rounded-lg px-4 py-2">
          <p className="font-mono text-xs text-terminal-red truncate">{error}</p>
        </div>
      )}

      {/* ── KPI header ─────────────────────────────────────────────── */}
      <section>
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-3">
          Rule Engine
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <KPICard label="Total Rules" value={total} highlight />
          <KPICard label="Enabled"  value={enabled}  positive={enabled > 0 ? true : undefined} />
          <KPICard label="Disabled" value={disabled} />
          <KPICard label="Buy Rules" value={buys} subLabel={`${total - buys} sell`} />
        </div>
      </section>

      {/* ── new rule / form ────────────────────────────────────────── */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
            {editing === 'new' ? 'New Rule' : editing ? `Edit Rule — ${editing.name}` : 'Rules'}
          </h2>
          {editing === null && (
            <button
              onClick={() => setEditing('new')}
              className="text-xs font-mono px-4 py-1.5 rounded bg-terminal-green/15 border border-terminal-green/40 text-terminal-green hover:bg-terminal-green/25 transition-colors"
            >
              + New Rule
            </button>
          )}
        </div>

        {editing === 'new' && (
          <RuleForm
            busy={busy === 'create'}
            onSubmit={(body) => run('create', () => onCreate(body))}
            onCancel={() => setEditing(null)}
          />
        )}
        {editing !== null && editing !== 'new' && (
          <RuleForm
            initial={editing}
            busy={busy === `update:${editing.id}`}
            onSubmit={(body) => run(`update:${editing.id}`, () => onUpdate(editing.id, body))}
            onCancel={() => setEditing(null)}
          />
        )}

        {editing === null && (
          <RulesTable
            rules={rules}
            loading={loading}
            busy={busy}
            onToggle={onToggle}
            onEdit={(r) => setEditing(r)}
            onDelete={onDelete}
          />
        )}
      </section>
    </div>
  )
}

// ── rules table ─────────────────────────────────────────────────────────────────

function RulesTable({
  rules, loading, busy, onToggle, onEdit, onDelete,
}: {
  rules: Rule[]
  loading: boolean
  busy: string | null
  onToggle: (r: Rule) => void
  onEdit: (r: Rule) => void
  onDelete: (r: Rule) => void
}) {
  if (loading) {
    return <p className="font-mono text-sm text-terminal-ghost py-6 text-center">Loading rules…</p>
  }
  if (rules.length === 0) {
    return (
      <p className="font-mono text-sm text-terminal-ghost py-6 text-center">
        No rules yet. Click “+ New Rule” to create your first one.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px]">
        <thead>
          <tr className="border-b border-terminal-border">
            {['', 'Name', 'Symbol', 'Action', 'Conditions', 'Cooldown', 'Last Triggered', ''].map((c, i) => (
              <th
                key={i}
                className="py-1.5 px-3 text-[10px] font-mono uppercase tracking-widest text-terminal-ghost font-normal text-left"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <RuleRow
              key={r.id}
              rule={r}
              busy={busy}
              onToggle={() => onToggle(r)}
              onEdit={() => onEdit(r)}
              onDelete={() => onDelete(r)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── rule row ────────────────────────────────────────────────────────────────────

function RuleRow({
  rule, busy, onToggle, onEdit, onDelete,
}: {
  rule: Rule
  busy: string | null
  onToggle: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  const isBuy = rule.action.type === 'BUY'
  const toggling = busy === `toggle:${rule.id}`
  const deleting = busy === `delete:${rule.id}`

  return (
    <tr className="border-b border-terminal-border/60 hover:bg-terminal-muted/20 transition-colors">
      {/* enable toggle */}
      <td className="py-2 px-3">
        <button
          onClick={onToggle}
          disabled={toggling}
          title={rule.enabled ? 'Disable rule' : 'Enable rule'}
          className={clsx(
            'relative inline-flex h-5 w-9 items-center rounded-full border transition-colors disabled:opacity-40',
            rule.enabled
              ? 'bg-terminal-green/20 border-terminal-green/50'
              : 'bg-terminal-muted/40 border-terminal-border',
          )}
        >
          <span
            className={clsx(
              'inline-block h-3.5 w-3.5 rounded-full transition-transform',
              rule.enabled ? 'translate-x-4 bg-terminal-green' : 'translate-x-1 bg-terminal-ghost',
            )}
          />
        </button>
      </td>

      {/* name + status */}
      <td className="py-2 px-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-semibold text-terminal-text">{rule.name}</span>
          <span
            className={clsx(
              'text-[9px] font-mono px-1.5 py-0.5 rounded border uppercase tracking-wider',
              rule.enabled
                ? 'text-terminal-green border-terminal-green/40 bg-terminal-green/10'
                : 'text-terminal-dim border-terminal-border bg-terminal-muted/30',
            )}
          >
            {rule.enabled ? 'on' : 'off'}
          </span>
        </div>
        <p className="font-mono text-[10px] text-terminal-ghost mt-0.5 truncate max-w-[280px]" title={condSummary(rule)}>
          {condSummary(rule)}
        </p>
      </td>

      {/* symbol */}
      <td className="py-2 px-3 font-mono text-xs text-terminal-dim">{rule.symbol}</td>

      {/* action */}
      <td className="py-2 px-3">
        <span className={clsx('font-mono text-xs font-semibold', isBuy ? 'text-terminal-green' : 'text-terminal-red')}>
          {rule.action.type} {rule.action.quantity}
        </span>
        <span className="font-mono text-[10px] text-terminal-ghost ml-1">{rule.action.order_type}</span>
      </td>

      {/* #conditions */}
      <td className="py-2 px-3 font-mono text-[11px] text-terminal-dim tabular-nums">
        {rule.conditions.length} <span className="text-terminal-ghost">({rule.logic})</span>
      </td>

      {/* cooldown */}
      <td className="py-2 px-3 font-mono text-[11px] text-terminal-dim tabular-nums">{rule.cooldown_minutes}m</td>

      {/* last triggered */}
      <td
        className="py-2 px-3 font-mono text-[11px] text-terminal-dim tabular-nums"
        title={fmtTime(rule.last_triggered)}
      >
        {relTime(rule.last_triggered)}
      </td>

      {/* controls */}
      <td className="py-2 px-3">
        <div className="flex items-center justify-end gap-1.5">
          <button
            onClick={onEdit}
            disabled={toggling || deleting}
            className="text-[11px] font-mono px-2.5 py-1 rounded border border-terminal-border text-terminal-dim hover:text-terminal-blue hover:border-terminal-blue/40 disabled:opacity-40 transition-colors"
          >
            Edit
          </button>
          <button
            onClick={onDelete}
            disabled={deleting || toggling}
            className="text-[11px] font-mono px-2.5 py-1 rounded border border-terminal-border text-terminal-dim hover:text-terminal-red hover:border-terminal-red/40 disabled:opacity-40 transition-colors"
          >
            {deleting ? '…' : 'Delete'}
          </button>
        </div>
      </td>
    </tr>
  )
}
