import React, { useState, useCallback } from 'react';

const INDICATORS = ['RSI', 'SMA', 'EMA', 'MACD', 'BBANDS', 'ATR', 'STOCH', 'PRICE'] as const;
const OPERATORS = ['crosses_above', 'crosses_below', '>', '<', '>=', '<='] as const;

interface ConditionRow {
  id: string;
  indicator: string;
  params: Record<string, number>;
  operator: string;
  value: string | number;
}

interface RuleBuilderProps {
  initialEntry?: ConditionRow[];
  initialExit?: ConditionRow[];
  onSave?: (data: { name: string; symbol: string; entry: ConditionRow[]; exit: ConditionRow[]; logic: string; action: { type: string; quantity: number; order_type: string } }) => void;
}

function newCondition(): ConditionRow {
  return { id: crypto.randomUUID(), indicator: 'RSI', params: { period: 14 }, operator: '<', value: 30 };
}

function ConditionEditor({ cond, onChange, onRemove }: {
  cond: ConditionRow; onChange: (c: ConditionRow) => void; onRemove: () => void;
}) {
  return (
    <div className="flex items-center gap-2 bg-zinc-800/50 rounded-lg p-2">
      <select value={cond.indicator} onChange={e => onChange({ ...cond, indicator: e.target.value })}
        className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200">
        {INDICATORS.map(i => <option key={i} value={i}>{i}</option>)}
      </select>

      {['RSI', 'SMA', 'EMA', 'ATR'].includes(cond.indicator) && (
        <input type="number" value={cond.params.period ?? 14} min={1} max={500}
          onChange={e => onChange({ ...cond, params: { ...cond.params, period: +e.target.value } })}
          className="w-16 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200" />
      )}

      <select value={cond.operator} onChange={e => onChange({ ...cond, operator: e.target.value })}
        className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200">
        {OPERATORS.map(o => <option key={o} value={o}>{o}</option>)}
      </select>

      <input value={cond.value} onChange={e => {
        const v = e.target.value;
        onChange({ ...cond, value: isNaN(+v) || v === '' ? v : +v });
      }}
        className="w-24 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-200"
        placeholder="30 or SMA_200" />

      <button onClick={onRemove} className="text-red-400 hover:text-red-300 text-sm px-1">✕</button>
    </div>
  );
}

export function RuleBuilder({ initialEntry, initialExit, onSave }: RuleBuilderProps) {
  const [name, setName] = useState('');
  const [symbol, setSymbol] = useState('');
  const [logic, setLogic] = useState('AND');
  const [actionType, setActionType] = useState('BUY');
  const [quantity, setQuantity] = useState(10);
  const [entry, setEntry] = useState<ConditionRow[]>(initialEntry ?? [newCondition()]);
  const [exit, setExit] = useState<ConditionRow[]>(initialExit ?? []);
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<{ valid?: boolean; errors?: any[]; warnings?: string[] } | null>(null);

  const updateEntry = useCallback((idx: number, c: ConditionRow) => {
    setEntry(prev => prev.map((r, i) => i === idx ? c : r));
  }, []);

  const updateExit = useCallback((idx: number, c: ConditionRow) => {
    setExit(prev => prev.map((r, i) => i === idx ? c : r));
  }, []);

  const handleValidate = async () => {
    setValidating(true);
    try {
      const res = await fetch('/api/rules/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conditions: entry }),
      });
      setValidation(await res.json());
    } catch { setValidation({ valid: false, errors: [{ message: 'Validation failed' }] }); }
    setValidating(false);
  };

  const handleSave = () => {
    onSave?.({
      name: name || 'Untitled Rule',
      symbol: symbol.toUpperCase(),
      entry,
      exit,
      logic,
      action: { type: actionType, quantity, order_type: 'MKT' },
    });
  };

  return (
    <div className="space-y-6">
      {/* Header inputs */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-zinc-400 mb-1">Rule Name</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="My Strategy"
            className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-zinc-200" />
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-400 mb-1">Symbol</label>
          <input value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="AAPL"
            className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-zinc-200" />
        </div>
      </div>

      {/* Action row */}
      <div className="flex items-center gap-4">
        <div className="flex gap-1">
          {['BUY', 'SELL'].map(a => (
            <button key={a} onClick={() => setActionType(a)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium ${actionType === a
                ? a === 'BUY' ? 'bg-emerald-600 text-white' : 'bg-red-600 text-white'
                : 'bg-zinc-800 text-zinc-400'}`}>{a}</button>
          ))}
        </div>
        <input type="number" value={quantity} min={1} onChange={e => setQuantity(+e.target.value)}
          className="w-20 px-2 py-1.5 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-zinc-200" />
        <span className="text-xs text-zinc-500">shares</span>
        <div className="ml-auto flex gap-1">
          {['AND', 'OR'].map(l => (
            <button key={l} onClick={() => setLogic(l)}
              className={`px-3 py-1 rounded text-xs font-medium ${logic === l ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400'}`}>{l}</button>
          ))}
        </div>
      </div>

      {/* Entry conditions */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-emerald-400">Entry Conditions</h3>
          <button onClick={() => setEntry(p => [...p, newCondition()])}
            disabled={entry.length >= 10}
            className="text-xs px-2 py-1 rounded bg-zinc-800 text-zinc-400 hover:text-zinc-200 disabled:opacity-30">
            + Add
          </button>
        </div>
        <div className="space-y-2">
          {entry.map((c, i) => (
            <ConditionEditor key={c.id} cond={c}
              onChange={u => updateEntry(i, u)}
              onRemove={() => setEntry(p => p.filter((_, j) => j !== i))} />
          ))}
        </div>
      </div>

      {/* Exit conditions */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-red-400">Exit Conditions</h3>
          <button onClick={() => setExit(p => [...p, newCondition()])}
            disabled={exit.length >= 10}
            className="text-xs px-2 py-1 rounded bg-zinc-800 text-zinc-400 hover:text-zinc-200 disabled:opacity-30">
            + Add
          </button>
        </div>
        <div className="space-y-2">
          {exit.length === 0 ? (
            <p className="text-xs text-zinc-500 italic">No exit conditions — positions will be held until manually closed.</p>
          ) : exit.map((c, i) => (
            <ConditionEditor key={c.id} cond={c}
              onChange={u => updateExit(i, u)}
              onRemove={() => setExit(p => p.filter((_, j) => j !== i))} />
          ))}
        </div>
      </div>

      {/* Validation result */}
      {validation && (
        <div className={`rounded-lg p-3 text-sm ${validation.valid ? 'bg-emerald-900/30 border border-emerald-700/50 text-emerald-300' : 'bg-red-900/30 border border-red-700/50 text-red-300'}`}>
          {validation.valid ? 'Conditions are valid!' : (
            <ul className="list-disc list-inside space-y-1">
              {validation.errors?.map((e, i) => <li key={i}>{e.message}{e.suggestion ? ` — ${e.suggestion}` : ''}</li>)}
            </ul>
          )}
          {validation.warnings?.map((w, i) => <p key={i} className="text-amber-400 mt-1">{w}</p>)}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3">
        <button onClick={handleValidate} disabled={validating || entry.length === 0}
          className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-sm disabled:opacity-40">
          {validating ? 'Validating...' : 'Validate'}
        </button>
        <button onClick={handleSave} disabled={!name || !symbol || entry.length === 0}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium disabled:opacity-40">
          Save Rule
        </button>
      </div>
    </div>
  );
}
