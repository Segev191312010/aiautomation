import React, { useState } from 'react';

interface MatchedTrade {
  symbol: string;
  entry_date: string;
  exit_date: string;
  entry_price: number;
  exit_price: number;
  qty: number;
  pnl: number;
  pnl_pct: number;
  hold_time: string;
}

export function TradeMatchTable({ trades }: { trades: MatchedTrade[] }) {
  const [sortKey, setSortKey] = useState<keyof MatchedTrade>('exit_date');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [filter, setFilter] = useState<'all' | 'winners' | 'losers'>('all');
  const [visibleCount, setVisibleCount] = useState(20);

  if (trades.length === 0) {
    return (
      <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-6 text-center">
        <p className="text-zinc-500 text-sm">No matched trades yet</p>
      </div>
    );
  }

  const filtered = trades.filter(t => {
    if (filter === 'winners') return t.pnl > 0;
    if (filter === 'losers') return t.pnl <= 0;
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (typeof av === 'number' && typeof bv === 'number') return sortDir === 'asc' ? av - bv : bv - av;
    return sortDir === 'asc' ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
  });

  const visible = sorted.slice(0, visibleCount);
  const totalPnl = filtered.reduce((s, t) => s + t.pnl, 0);

  const toggleSort = (key: keyof MatchedTrade) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('desc'); }
  };

  const Th = ({ k, children }: { k: keyof MatchedTrade; children: React.ReactNode }) => (
    <th className="px-3 py-2 text-left text-xs font-medium text-zinc-500 cursor-pointer hover:text-zinc-300"
      onClick={() => toggleSort(k)}>
      {children} {sortKey === k ? (sortDir === 'asc' ? '▲' : '▼') : ''}
    </th>
  );

  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between p-3 border-b border-zinc-800">
        <h3 className="text-sm font-semibold text-zinc-300">Matched Trades ({filtered.length})</h3>
        <div className="flex gap-1">
          {(['all', 'winners', 'losers'] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`px-2 py-1 rounded text-xs ${filter === f ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400'}`}>
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-zinc-800">
              <Th k="symbol">Symbol</Th>
              <Th k="entry_date">Entry</Th>
              <Th k="exit_date">Exit</Th>
              <Th k="entry_price">In</Th>
              <Th k="exit_price">Out</Th>
              <Th k="qty">Qty</Th>
              <Th k="pnl">P&L</Th>
              <Th k="pnl_pct">%</Th>
              <Th k="hold_time">Hold</Th>
            </tr>
          </thead>
          <tbody>
            {visible.map((t, i) => (
              <tr key={i} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                <td className="px-3 py-2 font-medium text-zinc-200">{t.symbol}</td>
                <td className="px-3 py-2 text-zinc-400">{t.entry_date?.slice(0, 10)}</td>
                <td className="px-3 py-2 text-zinc-400">{t.exit_date?.slice(0, 10)}</td>
                <td className="px-3 py-2 text-zinc-300">${t.entry_price.toFixed(2)}</td>
                <td className="px-3 py-2 text-zinc-300">${t.exit_price.toFixed(2)}</td>
                <td className="px-3 py-2 text-zinc-400">{t.qty}</td>
                <td className={`px-3 py-2 font-medium ${t.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  ${t.pnl.toFixed(2)}
                </td>
                <td className={`px-3 py-2 ${t.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {t.pnl_pct > 0 ? '+' : ''}{t.pnl_pct.toFixed(1)}%
                </td>
                <td className="px-3 py-2 text-zinc-500">{t.hold_time}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-zinc-700 bg-zinc-800/30">
              <td colSpan={6} className="px-3 py-2 text-zinc-400 font-medium">Total</td>
              <td className={`px-3 py-2 font-bold ${totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                ${totalPnl.toFixed(2)}
              </td>
              <td colSpan={2} />
            </tr>
          </tfoot>
        </table>
      </div>
      {sorted.length > visibleCount && (
        <button onClick={() => setVisibleCount(p => p + 20)}
          className="w-full py-2 text-xs text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/30">
          Load more ({sorted.length - visibleCount} remaining)
        </button>
      )}
    </div>
  );
}
