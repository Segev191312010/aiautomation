/**
 * RuleLabTab — Read-only rules table showing all AI-managed rules.
 * AI-created rules appear first. Operator can pause or retire rules.
 * Uses AutopilotRuleLab component for the full table + version history panel.
 */
import React, { useEffect, useState } from 'react'
import AutopilotRuleLab from '@/components/rules/AutopilotRuleLab'
import { fetchAutopilotRules } from '@/services/api'
import type { Rule } from '@/types'

export default function RuleLabTab() {
  const [rules, setRules] = useState<Rule[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function loadRules() {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchAutopilotRules()
      // AI-created rules first (created_by === 'ai'), then human rules
      const sorted = [...data].sort((a, b) => {
        const aIsAI = a.created_by === 'ai' ? 0 : 1
        const bIsAI = b.created_by === 'ai' ? 0 : 1
        return aIsAI - bIsAI
      })
      setRules(sorted)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load rules')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadRules()
  }, [])

  if (loading && !rules.length) {
    return (
      <div className="bg-white border border-[var(--border)] rounded-xl px-5 py-10 text-sm font-sans text-[var(--text-muted)]">
        Loading AI rule inventory...
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-sans text-red-700">
        {error}
      </div>
    )
  }

  return <AutopilotRuleLab rules={rules} onRefresh={loadRules} />
}
