import React from 'react'

interface Props {
  children: React.ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-64 gap-4 p-6">
          <div className="glass rounded-2xl shadow-glow-red p-8 flex flex-col items-center gap-4 max-w-md w-full">
            {/* Error icon */}
            <div className="w-10 h-10 rounded-full bg-terminal-red/15 flex items-center justify-center">
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 text-terminal-red">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z" />
              </svg>
            </div>

            <div className="text-center">
              <p className="text-sm font-sans font-semibold text-terminal-red mb-1">
                Something went wrong
              </p>
              <p className="text-xs font-sans text-terminal-ghost max-w-sm text-center leading-relaxed">
                {this.state.error?.message || 'An unexpected error occurred'}
              </p>
            </div>

            <button
              onClick={() => {
                this.setState({ hasError: false, error: null })
                window.location.reload()
              }}
              className={[
                'px-5 py-2 rounded-xl',
                'border border-terminal-blue/40 text-terminal-blue',
                'text-xs font-sans font-medium',
                'hover:bg-terminal-blue/10 transition-colors',
              ].join(' ')}
            >
              Reload
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
