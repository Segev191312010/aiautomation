import { useState, useEffect } from 'react'
import clsx from 'clsx'

const SECTIONS = [
  { id: 'section-overview', label: 'Overview' },
  { id: 'section-stats', label: 'Key Stats' },
  { id: 'section-rating', label: 'Rating' },
  { id: 'section-financials', label: 'Financials' },
  { id: 'section-company', label: 'Company' },
  { id: 'section-splits', label: 'Splits' },
  { id: 'section-analyst', label: 'Analyst' },
  { id: 'section-targets', label: 'Targets' },
  { id: 'section-analyst-detail', label: 'Grades' },
  { id: 'section-ownership', label: 'Ownership' },
  { id: 'section-narrative', label: 'Analysis' },
]

export default function SectionNav() {
  const [active, setActive] = useState(SECTIONS[0].id)

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActive(entry.target.id)
          }
        }
      },
      { rootMargin: '-20% 0px -70% 0px' },
    )

    for (const s of SECTIONS) {
      const el = document.getElementById(s.id)
      if (el) observer.observe(el)
    }

    return () => observer.disconnect()
  }, [])

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <nav className="sticky top-0 z-10 glass-elevated backdrop-blur-sm border-b border-white/[0.06] py-2 -mx-4 px-4 flex gap-1 overflow-x-auto">
      {SECTIONS.map((s) => (
        <button
          key={s.id}
          onClick={() => scrollTo(s.id)}
          className={clsx(
            'shrink-0 text-[10px] font-sans px-2.5 py-1 rounded-xl transition-colors',
            active === s.id
              ? 'bg-indigo-500/15 text-indigo-400 border border-indigo-500/30'
              : 'text-terminal-ghost hover:text-terminal-dim border border-transparent',
          )}
        >
          {s.label}
        </button>
      ))}
    </nav>
  )
}
