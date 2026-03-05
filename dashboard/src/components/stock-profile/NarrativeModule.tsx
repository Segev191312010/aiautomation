import type { StockNarrative } from '@/types'
import FreshnessTag from './FreshnessTag'

interface Props { data: StockNarrative | null; loading: boolean }

export default function NarrativeModule({ data, loading }: Props) {
  if (!data && loading) {
    return (
      <section className="glass rounded-2xl shadow-glass p-6 animate-pulse">
        <div className="h-3 w-36 bg-terminal-muted rounded-xl mb-4" />
        <div className="h-3 w-full bg-terminal-muted rounded-xl mb-2" />
        <div className="h-3 w-3/4 bg-terminal-muted rounded-xl mb-2" />
        <div className="h-3 w-5/6 bg-terminal-muted rounded-xl" />
      </section>
    )
  }
  if (!data) return null

  return (
    <section id="section-narrative" className="glass rounded-2xl shadow-glass p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-sans font-medium text-terminal-dim tracking-wide">Analysis Summary</h3>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <div>
          <span className="text-[9px] font-sans text-terminal-green uppercase tracking-wide">Strengths</span>
          <ul className="mt-1.5 flex flex-col gap-1">
            {data.strengths.map((s, i) => (
              <li key={i} className="text-[11px] font-sans text-terminal-dim flex items-start gap-1.5">
                <span className="text-terminal-green mt-0.5 shrink-0">+</span>
                <span>{s}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <span className="text-[9px] font-sans text-terminal-red uppercase tracking-wide">Risks</span>
          <ul className="mt-1.5 flex flex-col gap-1">
            {data.risks.map((r, i) => (
              <li key={i} className="text-[11px] font-sans text-terminal-dim flex items-start gap-1.5">
                <span className="text-terminal-red mt-0.5 shrink-0">-</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="border-t border-white/[0.06] pt-3">
        <span className="text-[9px] font-sans text-indigo-400 uppercase tracking-wide">Outlook</span>
        <p className="mt-1 text-[11px] font-sans text-terminal-dim leading-relaxed">{data.outlook}</p>
      </div>
    </section>
  )
}
