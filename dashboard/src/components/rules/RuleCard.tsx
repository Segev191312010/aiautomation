import type { Rule, Condition } from '@/types'

function conditionSummary(conditions: Condition[]): string {
  return conditions
    .map((c) => {
      const periodPart = c.params?.period != null ? `(${c.params.period})` : ''
      return `${c.indicator}${periodPart} ${c.operator} ${c.value}`
    })
    .join(' AND ')
}

interface Props {
  rule: Rule
  onToggle: () => void
  onEdit: () => void
  onClone: () => void
  onDelete: () => void
  onBacktest: () => void
}

export function RuleCard({ rule, onToggle, onEdit, onClone, onDelete, onBacktest }: Props) {
  const target = rule.symbol || rule.universe || 'No target'

  return (
    <div
      className={`rounded-xl border bg-zinc-900/80 p-4 transition-all ${
        rule.enabled ? 'border-blue-500/30' : 'border-zinc-800'
      }`}
    >
      {/* Header row */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-3">
          {/* Toggle switch */}
          <button
            onClick={onToggle}
            aria-label={rule.enabled ? 'Disable rule' : 'Enable rule'}
            className={`relative h-5 w-10 rounded-full transition-colors ${
              rule.enabled ? 'bg-blue-600' : 'bg-zinc-700'
            }`}
          >
            <span
              className={`absolute top-0.5 h-4 w-4 rounded-full bg-zinc-900 transition-transform ${
                rule.enabled ? 'left-5' : 'left-0.5'
              }`}
            />
          </button>
          <h3 className="font-semibold text-zinc-100">{rule.name}</h3>
        </div>
        <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs text-zinc-400">{target}</span>
      </div>

      {/* Entry conditions */}
      <div className="mb-1 text-xs text-zinc-500">
        <span className="text-green-400">Entry:</span>{' '}
        {rule.conditions.length > 0
          ? conditionSummary(rule.conditions)
          : <span className="italic text-zinc-600">no conditions</span>}
      </div>

      {/* Exit conditions — only render when present */}
      {rule.action && (
        <div className="mb-2 text-xs text-zinc-500">
          <span className="text-zinc-400">Action:</span>{' '}
          {rule.action.type} &times; {rule.action.quantity} ({rule.action.order_type})
        </div>
      )}

      {/* Logic badge */}
      <div className="mb-2">
        <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
          {rule.logic}
        </span>
        {rule.cooldown_minutes > 0 && (
          <span className="ml-2 text-[10px] text-zinc-600">
            cooldown {rule.cooldown_minutes}m
          </span>
        )}
      </div>

      {/* Footer */}
      <div className="mt-3 flex items-center justify-between border-t border-zinc-800 pt-3">
        <div className="text-xs text-zinc-500">
          {rule.last_triggered
            ? `Last: ${new Date(rule.last_triggered).toLocaleDateString()}`
            : 'Never triggered'}
        </div>
        <div className="flex gap-1">
          <button
            onClick={onBacktest}
            title="Backtest this rule"
            className="rounded bg-zinc-800 px-2 py-1 text-xs text-blue-400 hover:bg-zinc-700"
          >
            &#9654; Test
          </button>
          <button
            onClick={onClone}
            title="Clone rule"
            className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-700"
          >
            &#10697;
          </button>
          <button
            onClick={onEdit}
            title="Edit rule"
            className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-700"
          >
            &#9998;
          </button>
          <button
            onClick={onDelete}
            title="Delete rule"
            className="rounded bg-zinc-800 px-2 py-1 text-xs text-red-400 hover:bg-zinc-700"
          >
            &times;
          </button>
        </div>
      </div>
    </div>
  )
}
