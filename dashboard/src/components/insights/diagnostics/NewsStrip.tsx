import React from 'react'
import type { DiagnosticNewsArticle } from '@/types'

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleString()
}

export default function NewsStrip({ articles }: { articles: DiagnosticNewsArticle[] }) {
  return (
    <section className="card rounded-2xl shadow-card p-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-sans font-medium text-gray-500">Market News</h3>
        <span className="text-[10px] font-mono text-gray-400">{articles.length} items</span>
      </div>

      {articles.length === 0 ? (
        <div className="mt-3 text-[11px] font-mono text-gray-400">No recent news available.</div>
      ) : (
        <div className="mt-3 space-y-2">
          {articles.slice(0, 20).map((article) => (
            <a
              key={article.url}
              href={article.url}
              target="_blank"
              rel="noreferrer"
              className="block rounded-2xl border border-gray-200 bg-[#FAF8F5]/60 p-2.5 hover:border-indigo-600/30 transition-colors"
            >
              <div className="text-[11px] font-sans text-gray-500 line-clamp-2">{article.headline}</div>
              <div className="mt-1 text-[10px] font-mono text-gray-400">
                {article.source} · {fmtTs(article.published_at)}
              </div>
            </a>
          ))}
        </div>
      )}
    </section>
  )
}
