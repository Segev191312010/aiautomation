import React, { useState } from 'react';

interface SizeResult {
  shares: number;
  value: number;
  pct_of_portfolio: number;
  method: string;
}

export function PositionSizer() {
  const [entryPrice, setEntryPrice] = useState('');
  const [stopPrice, setStopPrice] = useState('');
  const [accountValue, setAccountValue] = useState('100000');
  const [riskPct, setRiskPct] = useState('1.0');
  const [method, setMethod] = useState('fixed_fractional');
  const [result, setResult] = useState<SizeResult | null>(null);
  const [loading, setLoading] = useState(false);

  const calculate = async () => {
    if (!entryPrice) return;
    setLoading(true);
    try {
      const res = await fetch('/api/risk/position-size', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          entry_price: +entryPrice,
          stop_price: stopPrice ? +stopPrice : null,
          account_value: +accountValue,
          risk_pct: +riskPct,
          method,
        }),
      });
      setResult(await res.json());
    } catch { /* ignore */ }
    setLoading(false);
  };

  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-zinc-300 mb-3">Position Size Calculator</h3>
      <div className="grid grid-cols-2 gap-3 mb-3">
        <div>
          <label className="text-[10px] text-zinc-500 mb-0.5 block">Entry Price</label>
          <input type="number" value={entryPrice} onChange={e => setEntryPrice(e.target.value)}
            placeholder="150.00"
            className="w-full px-2 py-1.5 bg-zinc-800 border border-zinc-700 rounded text-sm text-zinc-200" />
        </div>
        <div>
          <label className="text-[10px] text-zinc-500 mb-0.5 block">Stop Loss Price</label>
          <input type="number" value={stopPrice} onChange={e => setStopPrice(e.target.value)}
            placeholder="145.00"
            className="w-full px-2 py-1.5 bg-zinc-800 border border-zinc-700 rounded text-sm text-zinc-200" />
        </div>
        <div>
          <label className="text-[10px] text-zinc-500 mb-0.5 block">Account Value</label>
          <input type="number" value={accountValue} onChange={e => setAccountValue(e.target.value)}
            className="w-full px-2 py-1.5 bg-zinc-800 border border-zinc-700 rounded text-sm text-zinc-200" />
        </div>
        <div>
          <label className="text-[10px] text-zinc-500 mb-0.5 block">Risk %</label>
          <input type="number" value={riskPct} onChange={e => setRiskPct(e.target.value)} step="0.1"
            className="w-full px-2 py-1.5 bg-zinc-800 border border-zinc-700 rounded text-sm text-zinc-200" />
        </div>
      </div>

      <div className="flex items-center gap-2 mb-3">
        <select value={method} onChange={e => setMethod(e.target.value)}
          className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-sm text-zinc-200">
          <option value="fixed_fractional">Fixed Fractional</option>
          <option value="kelly">Kelly Criterion</option>
          <option value="equal_weight">Equal Weight</option>
          <option value="atr">ATR-Based</option>
        </select>
        <button onClick={calculate} disabled={loading || !entryPrice}
          className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded text-sm font-medium disabled:opacity-40">
          {loading ? '...' : 'Calculate'}
        </button>
      </div>

      {result && (
        <div className="bg-zinc-800/50 rounded-lg p-3 grid grid-cols-3 gap-3 text-center">
          <div>
            <div className="text-lg font-bold text-zinc-100">{result.shares}</div>
            <div className="text-[10px] text-zinc-500">Shares</div>
          </div>
          <div>
            <div className="text-lg font-bold text-zinc-100">${result.value.toLocaleString()}</div>
            <div className="text-[10px] text-zinc-500">Value</div>
          </div>
          <div>
            <div className="text-lg font-bold text-zinc-100">{result.pct_of_portfolio}%</div>
            <div className="text-[10px] text-zinc-500">Of Portfolio</div>
          </div>
        </div>
      )}
    </div>
  );
}
