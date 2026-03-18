import { useState, useEffect } from 'react'
import type { RuleTemplate, TemplateCategory } from '@/types'
import * as api from '@/services/api'

// TemplateCategory already includes 'all' in the union
const CATEGORIES: TemplateCategory[] = [
  'all',
  'trend_following',
  'mean_reversion',
  'momentum',
  'breakout',
  'composite',
]

function formatCategory(cat: TemplateCategory): string {
  return cat.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())
}

interface Props {
  onSelect: (template: RuleTemplate) => void
}

export function TemplateGallery({ onSelect }: Props) {
  const [templates, setTemplates] = useState<RuleTemplate[]>([])
  const [category, setCategory] = useState<TemplateCategory>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.fetchRuleTemplates()
      .then(setTemplates)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Failed to load templates'))
      .finally(() => setLoading(false))
  }, [])

  const filtered =
    category === 'all' ? templates : templates.filter((t) => t.category === category)

  return (
    <div className="space-y-4">
      {/* Category tabs */}
      <div className="flex gap-2 overflow-x-auto pb-2">
        {CATEGORIES.map((cat) => (
          <button
            key={cat}
            onClick={() => setCategory(cat)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium whitespace-nowrap transition-colors ${
              category === cat
                ? 'bg-blue-600 text-white'
                : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200'
            }`}
          >
            {formatCategory(cat)}
          </button>
        ))}
      </div>

      {/* Error state */}
      {error && (
        <div className="rounded-xl border border-red-800 bg-red-900/20 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-48 animate-pulse rounded-xl bg-zinc-800/50" />
          ))}
        </div>
      )}

      {/* Template grid */}
      {!loading && !error && (
        <>
          {filtered.length === 0 ? (
            <div className="py-12 text-center text-zinc-500">
              No templates in this category
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
              {filtered.map((template) => (
                <div
                  key={template.id}
                  className="group cursor-pointer rounded-xl border border-zinc-800 bg-zinc-900/80 p-4 transition-all hover:border-blue-500/50"
                  onClick={() => onSelect(template)}
                >
                  {/* Header */}
                  <div className="mb-2 flex items-start justify-between">
                    <h3 className="font-semibold text-zinc-100">{template.name}</h3>
                    <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[10px] text-zinc-400">
                      {formatCategory(template.category)}
                    </span>
                  </div>

                  {/* Description */}
                  <p className="mb-3 line-clamp-2 text-sm text-zinc-400">
                    {template.description}
                  </p>

                  {/* Indicators used */}
                  {template.indicators_used.length > 0 && (
                    <div className="mb-2 flex flex-wrap gap-1">
                      {template.indicators_used.map((ind) => (
                        <span
                          key={ind}
                          className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400"
                        >
                          {ind}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Footer */}
                  <div className="mt-auto flex items-center justify-between text-xs text-zinc-500">
                    <span className="flex items-center gap-1">
                      <span
                        className={
                          template.action_type === 'BUY' ? 'text-green-400' : 'text-red-400'
                        }
                      >
                        {template.action_type}
                      </span>
                      &bull; {template.logic}
                    </span>
                    <span className="text-blue-400 opacity-0 transition-opacity group-hover:opacity-100">
                      Use template &rarr;
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
