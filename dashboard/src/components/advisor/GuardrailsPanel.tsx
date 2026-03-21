/**
 * GuardrailsPanel — Configure AI autonomy guardrails and emergency stop.
 * Organized input grid with Save button. Emergency stop is prominently at top.
 * Data comes from props — no API calls within this component.
 */
import React, { useState, useEffect } from 'react'
import clsx from 'clsx'
import type { GuardrailConfig } from '@/types/advisor'

// ── Helpers ───────────────────────────────────────────────────────────────────

interface NumFieldProps {
  label:    string
  field:    keyof GuardrailConfig
  value:    number
  min?:     number
  max?:     number
  step?:    number
  suffix?:  string
  onChange: (field: keyof GuardrailConfig, value: number) => void
}

function NumField({ label, field, value, min, max, step = 1, suffix, onChange }: NumFieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[10px] font-sans font-medium text-[var(--text-muted)] uppercase tracking-wider">
        {label}
      </label>
      <div className="flex items-center gap-1">
        <input
          type="number"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(field, parseFloat(e.target.value) || 0)}
          className={clsx(
            'flex-1 text-xs font-mono text-[var(--text-primary)] bg-white',
            'border border-[var(--border)] rounded-lg px-2.5 py-1.5',
            'focus:outline-none focus:ring-1 focus:ring-[var(--accent)] focus:border-[var(--accent)]',
            'transition-colors min-w-0',
          )}
        />
        {suffix && (
          <span className="text-[10px] font-sans text-[var(--text-muted)] flex-shrink-0">{suffix}</span>
        )}
      </div>
    </div>
  )
}

// ── Section header ────────────────────────────────────────────────────────────

function SectionHeader({ title }: { title: string }) {
  return (
    <h4 className="text-[10px] font-sans font-semibold uppercase tracking-widest text-[var(--text-muted)] pt-1">
      {title}
    </h4>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  config:           GuardrailConfig | null
  onUpdate:         (config: Partial<GuardrailConfig>) => void | Promise<void>
  onEmergencyStop:  () => void
}

export default function GuardrailsPanel({ config, onUpdate, onEmergencyStop }: Props) {
  const [local, setLocal] = useState<GuardrailConfig | null>(config)

  // Sync when config prop changes
  useEffect(() => {
    setLocal(config)
  }, [config])

  function setField<K extends keyof GuardrailConfig>(field: K, value: GuardrailConfig[K]) {
    setLocal((prev) => prev ? { ...prev, [field]: value } : prev)
  }

  function handleNumChange(field: keyof GuardrailConfig, value: number) {
    setField(field, value as GuardrailConfig[typeof field])
  }

  const [saving, setSaving] = useState(false)

  async function handleSave() {
    if (!local || saving) return
    setSaving(true)
    try {
      await onUpdate(local)
    } finally {
      setSaving(false)
    }
  }

  if (!config || !local) {
    return (
      <div className="flex items-center justify-center py-8 text-sm font-sans text-[var(--text-muted)]">
        Guardrail configuration not available.
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {/* Emergency Stop */}
      <div className={clsx(
        'flex items-center justify-between px-4 py-3 rounded-xl border',
        local.emergency_stop
          ? 'bg-red-50 border-red-300'
          : 'bg-white border-[var(--border)]',
      )}>
        <div>
          <p className="text-sm font-sans font-semibold text-[var(--text-primary)]">Emergency Stop</p>
          <p className="text-[10px] font-sans text-[var(--text-muted)]">
            Halts all AI-driven changes immediately
          </p>
        </div>
        <button
          onClick={onEmergencyStop}
          className={clsx(
            'px-4 py-2 text-xs font-sans font-bold rounded-xl transition-colors',
            local.emergency_stop
              ? 'bg-red-600 text-white hover:bg-red-700'
              : 'bg-red-100 text-red-700 hover:bg-red-200',
          )}
        >
          {local.emergency_stop ? 'STOP ACTIVE' : 'Emergency Stop'}
        </button>
      </div>

      {/* AI Autonomy toggle */}
      <div className="flex items-center justify-between px-4 py-3 rounded-xl border border-[var(--border)] bg-white">
        <div>
          <p className="text-xs font-sans font-semibold text-[var(--text-primary)]">AI Autonomy</p>
          <p className="text-[10px] font-sans text-[var(--text-muted)]">Allow AI to apply changes automatically</p>
        </div>
        <button
          onClick={() => setField('ai_autonomy_enabled', !local.ai_autonomy_enabled)}
          className={clsx(
            'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
            local.ai_autonomy_enabled ? 'bg-emerald-500' : 'bg-gray-300',
          )}
        >
          <span className={clsx(
            'inline-block h-4 w-4 rounded-full bg-white shadow transition-transform',
            local.ai_autonomy_enabled ? 'translate-x-6' : 'translate-x-1',
          )} />
        </button>
      </div>

      {/* Parameter grid */}
      <div className="space-y-4">
        <SectionHeader title="Rules" />
        <div className="grid grid-cols-2 gap-3">
          <NumField label="Max Rules Disabled / Day" field="max_rules_disabled_per_day"
            value={local.max_rules_disabled_per_day} min={0} max={20}
            onChange={handleNumChange} />
          <NumField label="Max Rules Enabled / Day" field="max_rules_enabled_per_day"
            value={local.max_rules_enabled_per_day} min={0} max={20}
            onChange={handleNumChange} />
        </div>

        <SectionHeader title="Risk" />
        <div className="grid grid-cols-3 gap-3">
          <NumField label="Max Position Size Increase" field="max_position_size_increase_pct"
            value={local.max_position_size_increase_pct} min={0} max={100} step={5}
            suffix="%" onChange={handleNumChange} />
          <NumField label="Max Weight Change" field="max_weight_change_pct"
            value={local.max_weight_change_pct} min={0} max={100} step={5}
            suffix="%" onChange={handleNumChange} />
          <NumField label="Max ATR Mult Change" field="max_atr_mult_change"
            value={local.max_atr_mult_change} min={0} max={5} step={0.1}
            onChange={handleNumChange} />
        </div>

        <SectionHeader title="Scoring" />
        <div className="grid grid-cols-2 gap-3">
          <NumField label="Min Score Floor" field="min_score_floor"
            value={local.min_score_floor} min={0} max={100}
            onChange={handleNumChange} />
          <NumField label="Min Score Ceiling" field="min_score_ceiling"
            value={local.min_score_ceiling} min={0} max={100}
            onChange={handleNumChange} />
        </div>

        <SectionHeader title="Limits" />
        <div className="grid grid-cols-2 gap-3">
          <NumField label="Max Changes / Day" field="max_changes_per_day"
            value={local.max_changes_per_day} min={1} max={50}
            onChange={handleNumChange} />
          <NumField label="Min Hours Between Changes" field="min_hours_between_changes"
            value={local.min_hours_between_changes} min={0} max={72} step={0.5}
            suffix="h" onChange={handleNumChange} />
        </div>
      </div>

      {/* Save */}
      <button
        onClick={handleSave}
        disabled={saving}
        className="w-full py-2.5 text-sm font-sans font-semibold rounded-xl transition-colors
                   bg-[var(--accent)] text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {saving ? 'Saving...' : 'Save Guardrails'}
      </button>
    </div>
  )
}
