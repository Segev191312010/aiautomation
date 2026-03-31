import clsx from 'clsx'
import { fmtUSD, fmtTimestamp } from '@/utils/formatters'
import type { Trade } from '@/types'

const STATUS_DOT: Record<string, string> = {
  FILLED:    'bg-emerald-400',
  PENDING:   'bg-amber-600',
  CANCELLED: 'bg-zinc-600',
  ERROR:     'bg-red-400',
}

const STATUS_BADGE: Record<string, string> = {
  FILLED:    'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  PENDING:   'text-amber-600 bg-amber-500/10 border-amber-500/20',
  CANCELLED: 'text-zinc-500 bg-zinc-800/60 border-zinc-800',
  ERROR:     'text-red-400 bg-red-500/10 border-red-500/20',
}

interface TradeRowProps {
  trade: Trade
}

export function TradeRow({ trade }: TradeRowProps) {
  const isBuy = trade.action === 'BUY'
  const dotClass   = STATUS_DOT[trade.status]   ?? 'bg-zinc-600'
  const badgeClass = STATUS_BADGE[trade.status] ?? 'text-zinc-500 bg-zinc-800/60 border-zinc-800'

  return (
    <tr
      className={clsx(
        'border-b border-zinc-800 transition-colors group',
        isBuy
          ? 'hover:bg-emerald-500/[0.04]'
          : 'hover:bg-red-500/[0.04]',
      )}
    >
      <td className="py-2.5 px-3 font-mono text-[11px] text-zinc-500 tabular-nums whitespace-nowrap">
        {fmtTimestamp(trade.timestamp)}
      </td>
      <td className="py-2.5 px-3 font-mono text-sm font-semibold text-zinc-100 tracking-wide">
        {trade.symbol}
      </td>
      <td className="py-2.5 px-3">
        <span
          className={clsx(
            'inline-flex items-center gap-1.5 font-mono text-xs font-semibold px-2 py-0.5 rounded-lg border',
            isBuy
              ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
              : 'text-red-400 bg-red-500/10 border-red-500/20',
          )}
        >
          <span
            className={clsx(
              'w-1.5 h-1.5 rounded-full flex-shrink-0',
              isBuy ? 'bg-emerald-400' : 'bg-red-400',
            )}
          />
          {trade.action}
        </span>
      </td>
      <td className="py-2.5 px-3 font-mono text-sm text-zinc-400 tabular-nums text-right">
        {trade.quantity}
      </td>
      <td className="py-2.5 px-3 font-mono text-sm text-zinc-400 tabular-nums text-right">
        {trade.fill_price != null ? fmtUSD(trade.fill_price) : (
          <span className="text-zinc-500">—</span>
        )}
      </td>
      <td className="py-2.5 px-3 text-right">
        <span
          className={clsx(
            'inline-flex items-center gap-1.5 font-mono text-[11px] font-medium px-2 py-0.5 rounded-lg border',
            badgeClass,
          )}
        >
          <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0', dotClass)} />
          {trade.status}
        </span>
      </td>
    </tr>
  )
}
