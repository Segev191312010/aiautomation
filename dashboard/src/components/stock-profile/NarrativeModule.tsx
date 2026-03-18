import type { StockNarrative } from '@/types'
import FreshnessTag from './FreshnessTag'

// ── SVG Icons ─────────────────────────────────────────────────────────────────

function CheckCircleIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 15 15"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className="shrink-0 mt-px"
    >
      <circle cx="7.5" cy="7.5" r="6.75" stroke="currentColor" strokeWidth="1.25" />
      <path
        d="M4.5 7.5L6.5 9.5L10.5 5.5"
        stroke="currentColor"
        strokeWidth="1.35"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function WarningTriangleIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 15 15"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className="shrink-0 mt-px"
    >
      <path
        d="M7.5 2L13.5 12.5H1.5L7.5 2Z"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinejoin="round"
      />
      <path
        d="M7.5 6V8.5"
        stroke="currentColor"
        strokeWidth="1.35"
        strokeLinecap="round"
      />
      <circle cx="7.5" cy="10.5" r="0.65" fill="currentColor" />
    </svg>
  )
}

function InfoCircleIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 15 15"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className="shrink-0 mt-px"
    >
      <circle cx="7.5" cy="7.5" r="6.75" stroke="currentColor" strokeWidth="1.25" />
      <path
        d="M7.5 6.5V10.5"
        stroke="currentColor"
        strokeWidth="1.35"
        strokeLinecap="round"
      />
      <circle cx="7.5" cy="4.75" r="0.7" fill="currentColor" />
    </svg>
  )
}

// ── Section header icons (larger) ────────────────────────────────────────────

function CheckCircleHeaderIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 15 15"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className="shrink-0"
    >
      <circle cx="7.5" cy="7.5" r="6.75" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M4.5 7.5L6.5 9.5L10.5 5.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function WarningTriangleHeaderIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 15 15"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className="shrink-0"
    >
      <path
        d="M7.5 2L13.5 12.5H1.5L7.5 2Z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path
        d="M7.5 6V8.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <circle cx="7.5" cy="10.5" r="0.65" fill="currentColor" />
    </svg>
  )
}

function InfoCircleHeaderIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 15 15"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className="shrink-0"
    >
      <circle cx="7.5" cy="7.5" r="6.75" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M7.5 6.5V10.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <circle cx="7.5" cy="4.75" r="0.7" fill="currentColor" />
    </svg>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

interface StrengthItemProps {
  text: string
}

function StrengthItem({ text }: StrengthItemProps) {
  return (
    <li className="flex items-start gap-2.5 px-3 py-2 rounded-lg transition-colors duration-100 hover:bg-emerald-600/[0.06] group">
      <span className="text-emerald-400 mt-[1px]">
        <CheckCircleIcon />
      </span>
      <span className="text-[12px] font-sans text-zinc-400 leading-relaxed group-hover:text-zinc-100 transition-colors duration-100">
        {text}
      </span>
    </li>
  )
}

interface RiskItemProps {
  text: string
}

function RiskItem({ text }: RiskItemProps) {
  return (
    <li className="flex items-start gap-2.5 px-3 py-2 rounded-lg transition-colors duration-100 hover:bg-red-600/[0.06] group">
      <span className="text-red-400 mt-[1px]">
        <WarningTriangleIcon />
      </span>
      <span className="text-[12px] font-sans text-zinc-400 leading-relaxed group-hover:text-zinc-100 transition-colors duration-100">
        {text}
      </span>
    </li>
  )
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <section className="card rounded-lg  p-6 animate-pulse">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div className="h-3 w-44 bg-zinc-800 rounded-lg" />
        <div className="h-3 w-12 bg-zinc-800 rounded-lg" />
      </div>
      {/* Two-col cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <div className="bg-zinc-900 rounded-xl p-4 border border-zinc-800">
          <div className="h-3 w-20 bg-zinc-800 rounded-lg mb-3" />
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-3 bg-zinc-800 rounded-lg mb-2 last:mb-0" style={{ width: `${75 + (i % 2) * 15}%` }} />
          ))}
        </div>
        <div className="bg-zinc-900 rounded-xl p-4 border border-zinc-800">
          <div className="h-3 w-16 bg-zinc-800 rounded-lg mb-3" />
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-3 bg-zinc-800 rounded-lg mb-2 last:mb-0" style={{ width: `${70 + (i % 3) * 10}%` }} />
          ))}
        </div>
      </div>
      {/* Outlook card */}
      <div className="bg-zinc-900 rounded-xl p-4 border border-zinc-800">
        <div className="h-3 w-16 bg-zinc-800 rounded-lg mb-3" />
        <div className="h-3 w-full bg-zinc-800 rounded-lg mb-2" />
        <div className="h-3 w-5/6 bg-zinc-800 rounded-lg mb-2" />
        <div className="h-3 w-4/5 bg-zinc-800 rounded-lg" />
      </div>
    </section>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  data: StockNarrative | null
  loading: boolean
}

export default function NarrativeModule({ data, loading }: Props) {
  if (!data && loading) return <LoadingSkeleton />
  if (!data) return null

  return (
    <section
      id="section-narrative"
      className="card rounded-lg  p-6"
    >
      {/* Module header */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-sans font-semibold text-zinc-400 tracking-wide uppercase">
            Executive Summary
          </h3>
        </div>
        <FreshnessTag fetchedAt={data.fetched_at} />
      </div>

      {/* Strengths + Risks grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">

        {/* Strengths card */}
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 overflow-hidden">
          <div className="flex items-center gap-2 px-4 pt-3.5 pb-2.5 border-b border-zinc-800">
            <span className="text-emerald-400">
              <CheckCircleHeaderIcon />
            </span>
            <span className="text-[10px] font-sans font-semibold text-emerald-400 uppercase tracking-widest">
              Strengths
            </span>
          </div>
          {data.strengths.length > 0 ? (
            <ul className="py-1.5 px-1">
              {data.strengths.map((s, i) => (
                <StrengthItem key={i} text={s} />
              ))}
            </ul>
          ) : (
            <p className="text-[11px] font-sans text-zinc-500 px-4 py-3">
              No strengths identified.
            </p>
          )}
        </div>

        {/* Risks card */}
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 overflow-hidden">
          <div className="flex items-center gap-2 px-4 pt-3.5 pb-2.5 border-b border-zinc-800">
            <span className="text-red-400">
              <WarningTriangleHeaderIcon />
            </span>
            <span className="text-[10px] font-sans font-semibold text-red-400 uppercase tracking-widest">
              Risks
            </span>
          </div>
          {data.risks.length > 0 ? (
            <ul className="py-1.5 px-1">
              {data.risks.map((r, i) => (
                <RiskItem key={i} text={r} />
              ))}
            </ul>
          ) : (
            <p className="text-[11px] font-sans text-zinc-500 px-4 py-3">
              No risks identified.
            </p>
          )}
        </div>
      </div>

      {/* Outlook card */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 overflow-hidden">
        <div className="flex items-center gap-2 px-4 pt-3.5 pb-2.5 border-b border-zinc-800">
          <span className="text-indigo-600">
            <InfoCircleHeaderIcon />
          </span>
          <span className="text-[10px] font-sans font-semibold text-indigo-600 uppercase tracking-widest">
            Outlook
          </span>
        </div>
        <div className="flex items-start gap-3 px-4 py-4">
          <span className="text-indigo-600 mt-0.5 shrink-0">
            <InfoCircleIcon />
          </span>
          <p className="text-[13px] font-sans text-zinc-100 leading-relaxed">
            {data.outlook}
          </p>
        </div>
      </div>
    </section>
  )
}
