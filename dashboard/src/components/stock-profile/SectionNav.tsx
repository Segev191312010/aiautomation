import { useEffect, useState } from 'react'
import clsx from 'clsx'

const SECTIONS = [
  { id: 'section-overview', label: 'Overview' },
  { id: 'section-stats', label: 'Key Stats' },
  { id: 'section-financials', label: 'Financials' },
  { id: 'section-company', label: 'Company' },
  { id: 'section-narrative', label: 'Narrative' },
  { id: 'section-events', label: 'Events' },
  { id: 'section-splits', label: 'Splits' },
  { id: 'section-rating', label: 'Fundamentals' },
  { id: 'section-analyst', label: 'Analysts' },
  { id: 'section-targets', label: 'Targets' },
  { id: 'section-ownership', label: 'Ownership' },
]

export default function SectionNav() {
  const [active, setActive] = useState(SECTIONS[0].id)

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActive(entry.target.id)
            break
          }
        }
      },
      { rootMargin: '-25% 0px -60% 0px', threshold: 0.15 },
    )

    for (const section of SECTIONS) {
      const el = document.getElementById(section.id)
      if (el) observer.observe(el)
    }
    return () => observer.disconnect()
  }, [])

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <nav className="sticky top-0 z-10 -mx-4 px-4 py-2 bg-[#FAF8F5]/95 border-b border-zinc-800 overflow-x-auto">
      <div className="flex items-center gap-1.5 min-w-max">
        {SECTIONS.map((section) => (
          <button
            key={section.id}
            onClick={() => scrollTo(section.id)}
            className={clsx(
              'shrink-0 text-[11px] font-sans px-2.5 py-1 rounded-lg border transition-colors',
              active === section.id
                ? 'bg-zinc-950 text-white border-zinc-800'
                : 'bg-zinc-900 text-zinc-400 border-zinc-800 hover:text-zinc-50 hover:border-zinc-800',
            )}
          >
            {section.label}
          </button>
        ))}
      </div>
    </nav>
  )
}
