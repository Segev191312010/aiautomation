import React from 'react'
import type { DiagnosticNewsArticle } from '@/types'

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleString()
}

export default function NewsStrip({ articles }: { articles: DiagnosticNewsArticle[] }) {
  return (
    <section className="card rounded-2xl  p-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-sans font-medium text-zinc-400">Market News</h3>
        <span className="text-[10px] font-mono text-zinc-500">{articles.length} items</span>
      </div>

      {articles.length === 0 ? (
        <div className="mt-3 text-[11px] font-mono text-zinc-500">No recent news available.</div>
      ) : (
        <div className="mt-3 space-y-2">
          {articles.slice(0, 20).map((article) => (
            <a
              key={article.url}
              href={article.url}
              target="_blank"
              rel="noreferrer"
              className="block rounded-2xl border border-zinc-800 bg-[#FAF8F5]/60 p-2.5 hover:border-indigo-600/30 transition-colors"
            >
              <div className="text-[11px] font-sans text-zinc-400 line-clamp-2">{article.headline}</div>
              <div className="mt-1 text-[10px] font-mono text-zinc-500">
                {article.source} · {fmtTs(article.published_at)}
              </div>
            </a>
          ))}
        </div>
      )}
    </section>
  )
}
