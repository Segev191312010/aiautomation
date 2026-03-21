/**
 * RecommendationsPanel — Grouped recommendation cards for the AI Advisor.
 * Groups by priority: high → medium → low.
 * Each card has a colored left border and type icon.
 * Data comes from props — no API calls.
 */
import React from 'react'
import clsx from 'clsx'
import type { Recommendation, RecommendationPriority, RecommendationType } from '@/types/advisor'

// ── Icons ─────────────────────────────────────────────────────────────────────

function IconX() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}
      strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  )
}

function IconArrowUp() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}
      strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
      <line x1="12" y1="19" x2="12" y2="5" />
      <polyline points="5 12 12 5 19 12" />
    </svg>
  )
}

function IconSettings() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  )
}

function IconTriangle() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  )
}

// ── Styles maps ───────────────────────────────────────────────────────────────

const PRIORITY_STYLES: Record<RecommendationPriority, string> = {
  high:   'border-l-4 border-l-red-500 bg-red-50',
  medium: 'border-l-4 border-l-amber-500 bg-amber-50',
  low:    'border-l-4 border-l-indigo-500 bg-indigo-50',
}

const PRIORITY_LABEL: Record<RecommendationPriority, string> = {
  high:   'text-red-600',
  medium: 'text-amber-700',
  low:    'text-indigo-600',
}

const TYPE_ICON: Record<RecommendationType, React.ReactNode> = {
  disable: <IconX />,
  boost:   <IconArrowUp />,
  adjust:  <IconSettings />,
  warning: <IconTriangle />,
}

// ── Single recommendation card ────────────────────────────────────────────────

function RecCard({ rec }: { rec: Recommendation }) {
  return (
    <div className={clsx('rounded-xl p-3.5 flex items-start gap-3', PRIORITY_STYLES[rec.priority])}>
      <span className={clsx('flex-shrink-0 mt-0.5', PRIORITY_LABEL[rec.priority])}>
        {TYPE_ICON[rec.type]}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className={clsx(
            'text-[9px] font-sans font-semibold uppercase tracking-widest px-1.5 py-0.5 rounded',
            rec.priority === 'high'   ? 'bg-red-100 text-red-700' :
            rec.priority === 'medium' ? 'bg-amber-100 text-amber-800' :
                                        'bg-indigo-100 text-indigo-700',
          )}>
            {rec.category}
          </span>
          <span className={clsx(
            'text-[9px] font-sans uppercase tracking-widest',
            PRIORITY_LABEL[rec.priority],
          )}>
            {rec.priority}
          </span>
        </div>
        <p className="text-xs font-sans text-[var(--text-primary)] leading-relaxed">
          {rec.message}
        </p>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  recommendations: Recommendation[]
}

const PRIORITY_ORDER: RecommendationPriority[] = ['high', 'medium', 'low']

export default function RecommendationsPanel({ recommendations }: Props) {
  if (!recommendations || recommendations.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-sm font-sans text-[var(--text-muted)]">
        No recommendations — system looks healthy.
      </div>
    )
  }

  const grouped = PRIORITY_ORDER.reduce<Record<RecommendationPriority, Recommendation[]>>(
    (acc, p) => {
      acc[p] = recommendations.filter((r) => r.priority === p)
      return acc
    },
    { high: [], medium: [], low: [] },
  )

  return (
    <div className="space-y-2">
      {PRIORITY_ORDER.map((priority) =>
        grouped[priority].map((rec, i) => (
          <RecCard key={`${priority}-${i}`} rec={rec} />
        )),
      )}
    </div>
  )
}
