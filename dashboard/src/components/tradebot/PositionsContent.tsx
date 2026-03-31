import { SectionHeader } from '@/components/common/SectionHeader'
import { QuickOrderForm } from '@/components/tradebot/QuickOrderForm'
import PositionsTable from '@/components/tradebot/PositionsTable'
import { IconLightning, IconBriefcase } from '@/components/icons'
import type { Position, SimPosition } from '@/types'

function PositionsEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-14 gap-3">
      <div className="w-14 h-14 rounded-2xl bg-zinc-800/30 flex items-center justify-center">
        <IconBriefcase className="w-7 h-7 text-zinc-500/50" />
      </div>
      <p className="text-sm font-sans text-zinc-500">No open positions</p>
      <p className="text-[11px] font-sans text-zinc-500/60">
        Use the Quick Order form to enter a trade
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
      {/* Quick order */}
      <section className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 animate-fade-in-up">
        <SectionHeader
          eyebrow=""
          icon={<IconLightning className="w-3.5 h-3.5 text-amber-600" />}
          title="Quick Order"
        />
        <QuickOrderForm />
      </section>

      {/* Positions table */}
      <section className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 animate-fade-in-up">
        <SectionHeader
          eyebrow=""
          icon={<IconBriefcase className="w-3.5 h-3.5 text-zinc-400" />}
          title="Open Positions"
          badge={
            positions.length > 0 ? (
              <span className="ml-auto text-[11px] font-mono text-zinc-500 bg-zinc-800/50 px-2 py-0.5 rounded-lg">
                {positions.length}
              </span>
            ) : undefined
          }
        />
        {positions.length === 0 && !initialLoad
          ? <PositionsEmptyState />
          : <PositionsTable />
        }
      </section>
    </div>
  )
}
