import clsx from 'clsx'
import { Q_COLORS, Q_LABEL, type Quadrant } from './constants'

export function QuadrantBadge({ quadrant }: { quadrant: Quadrant }) {
  const c = Q_COLORS[quadrant]
  return (
    <span className={clsx(
      'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-mono font-medium uppercase tracking-wider',
      c.badge,
    )}>
      <span className={clsx('h-1.5 w-1.5 rounded-full shrink-0', c.dot)} />
      {Q_LABEL[quadrant]}
    </span>
  )
}
