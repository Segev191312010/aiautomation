import { SectionHeader } from '@/components/common/SectionHeader'
import { QuickOrderForm } from '@/components/tradebot/QuickOrderForm'
import PositionsTable from '@/components/tradebot/PositionsTable'
import { IconLightning, IconBriefcase } from '@/components/icons'
import type { Position, SimPosition } from '@/types'

function PositionsEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-14">
      <div className="flex h-14 w-14 items-center justify-center rounded-3xl border border-[var(--border)] bg-[var(--bg-hover)]">
        <IconBriefcase className="h-7 w-7 text-[var(--text-muted)]" />
      </div>
      <p className="text-sm font-sans text-[var(--text-secondary)]">No open positions</p>
      <p className="text-[11px] font-sans text-[var(--text-muted)]">
        Use the quick order rail to enter a trade.
      </p>
    </div>
  )
}

interface PositionsContentProps {
  positions: (Position | SimPosition)[]
  initialLoad: boolean
}

export function PositionsContent({ positions, initialLoad }: PositionsContentProps) {
  return (
    <div className="flex flex-col gap-5">
      <section className="shell-panel gradient-surface animate-fade-in-up p-5 sm:p-6">
        <SectionHeader
          eyebrow="Execution"
          icon={<IconLightning className="h-3.5 w-3.5 text-amber-600" />}
          title="Quick Order"
        />
        <QuickOrderForm />
      </section>

      <section className="shell-panel animate-fade-in-up p-5 sm:p-6">
        <SectionHeader
          eyebrow="Book"
          icon={<IconBriefcase className="h-3.5 w-3.5 text-[var(--text-secondary)]" />}
          title="Open Positions"
          badge={
            positions.length > 0 ? (
              <span className="shell-chip px-3 py-1 text-[11px] font-mono">{positions.length}</span>
            ) : undefined
          }
        />

        {positions.length === 0 && !initialLoad ? <PositionsEmptyState /> : <PositionsTable />}
      </section>
    </div>
  )
}
