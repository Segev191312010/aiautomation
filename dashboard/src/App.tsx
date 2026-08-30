const modules = [
  {
    name: 'Market intake',
    detail: 'Normalized snapshots and scanner ports feed deterministic strategy evaluation.',
    state: 'Contract ready',
  },
  {
    name: 'Strategy lifecycle',
    detail: 'Draft, backtest, shadow, pause, and retire transitions are explicit and versioned.',
    state: 'Active',
  },
  {
    name: 'Risk authority',
    detail: 'Position, portfolio, and daily-loss limits remain deterministic and fail closed.',
    state: 'Active',
  },
  {
    name: 'Decision ledger',
    detail: 'Every proposal records its inputs, fingerprint, disposition, and explanation.',
    state: 'Active',
  },
  {
    name: 'Broker adapter',
    detail: 'Execution is isolated behind a port. Automated order authority is not enabled.',
    state: 'Locked',
  },
  {
    name: 'Learning evidence',
    detail: 'Outcome analytics exist; promotion still requires current-SHA evidence.',
    state: 'Evidence pending',
  },
]

export default function App() {
  return (
    <main className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Clean rebuild baseline</p>
          <h1>Autotrader V1</h1>
          <p className="lede">
            A small, auditable trading engine built around explicit strategy states,
            deterministic risk, and evidence-gated execution authority.
          </p>
        </div>
        <div className="mode-card" aria-label="Current engine authority">
          <span className="status-dot" />
          <div>
            <strong>Backtest + Shadow</strong>
            <span>Order execution disabled</span>
          </div>
        </div>
      </header>

      <section className="fence-grid" aria-label="Authority fences">
        <article>
          <span>Automated PAPER</span>
          <strong>Locked</strong>
          <p>Requires reviewed Phase-5 evidence for the running source SHA.</p>
        </article>
        <article>
          <span>LIVE</span>
          <strong>Hard NO-GO</strong>
          <p>Requires the complete independent release conjunction and owner approval.</p>
        </article>
        <article>
          <span>Current objective</span>
          <strong>Prove the engine</strong>
          <p>Build rules, replay them, shadow decisions, and measure outcomes.</p>
        </article>
      </section>

      <section>
        <div className="section-heading">
          <div>
            <p className="eyebrow">Retained kernel</p>
            <h2>System modules</h2>
          </div>
          <span>{modules.length} bounded modules</span>
        </div>
        <div className="module-grid">
          {modules.map((module) => (
            <article className="module-card" key={module.name}>
              <div>
                <h3>{module.name}</h3>
                <span>{module.state}</span>
              </div>
              <p>{module.detail}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="next-step">
        <p className="eyebrow">Next build slice</p>
        <h2>Scanner → rule proposal → deterministic risk → shadow decision</h2>
        <p>
          No legacy bot loop is connected. The next implementation work is measured against
          this single pipeline and its evidence, not the retired monolith.
        </p>
      </section>
    </main>
  )
}
