import type {
  AutopilotConfig,
  AutopilotIntervention,
  AutopilotPerformance,
  AuditLogPage,
  AIStatus,
  AIRuleAction,
  AIDirectTrade,
  RulePerformanceRow,
  RulePromotionReadiness,
  RuleValidationRecord,
  RuleVersionRecord,
  CostReport,
  SourcePerformance,
  LearningMetrics,
  EconomicReport,
  DecisionRun,
  DecisionItem,
  EvaluationRun,
  EvaluationSlice,
  EvaluationCompare,
  ReplayRequest,
} from '@/types/advisor'
import type { Rule, Trade } from '@/types'
import { get, post, put } from './client'

// Guardrails & mode
export const fetchGuardrails = () =>
  get<AutopilotConfig>('/api/autopilot/config')

export const updateGuardrails = (config: Partial<AutopilotConfig>) =>
  put<AutopilotConfig>('/api/autopilot/config', config)

export const postEmergencyStop = () =>
  post<{ emergency_stop: boolean; message: string }>('/api/autopilot/kill')

export const resetEmergencyStop = () =>
  post<{ emergency_stop: boolean; message: string }>('/api/autopilot/kill/reset')

export const setAutopilotMode = (mode: 'OFF' | 'PAPER' | 'LIVE', reason = '') =>
  post<AutopilotConfig>('/api/autopilot/mode', { mode, reason })

export const resetDailyLossLock = () =>
  post<AutopilotConfig>('/api/autopilot/daily-loss/reset')

// Audit log
export const fetchAuditLog = (limit = 50, offset = 0) =>
  get<AuditLogPage>(`/api/autopilot/feed?limit=${limit}&offset=${offset}`)

export const revertAIAction = (entryId: number) =>
  post<{ reverted: boolean }>(`/api/autopilot/feed/${entryId}/revert`)

// AI status & costs
export const fetchAIStatus = () =>
  get<AIStatus>('/api/autopilot/status')

export const fetchAICosts = (days = 30) =>
  get<CostReport>(`/api/autopilot/costs?days=${days}`)

export const fetchLearningMetrics = (windowDays = 30) =>
  get<LearningMetrics>(`/api/autopilot/learning-metrics?window_days=${windowDays}`)

export const fetchEconomicReport = (days = 30) =>
  get<EconomicReport>(`/api/autopilot/economic-report?days=${days}`)

// Autopilot rules
export const fetchAutopilotRules = () =>
  get<Rule[]>('/api/autopilot/rules')

export const fetchAutopilotRule = (id: string) =>
  get<Rule>(`/api/autopilot/rules/${id}`)

export const fetchAutopilotRuleVersions = (id: string) =>
  get<RuleVersionRecord[]>(`/api/autopilot/rules/${id}/versions`)

export const fetchAutopilotRuleValidations = (id: string) =>
  get<RuleValidationRecord[]>(`/api/autopilot/rules/${id}/validations`)

export const fetchAutopilotRulePromotionReadiness = (id: string) =>
  get<RulePromotionReadiness>(`/api/autopilot/rules/${id}/promotion-readiness`)

export const manualPauseAutopilotRule = (id: string, reason = '') =>
  post<Rule>(`/api/autopilot/rules/${id}/manual-pause`, { reason })

export const manualRetireAutopilotRule = (id: string, reason = '') =>
  post<Rule>(`/api/autopilot/rules/${id}/manual-retire`, { reason })

export const applyRuleLabActions = (actions: AIRuleAction[], author = 'ai', allowActive = false) =>
  post<{ results: Array<Record<string, unknown>> }>('/api/autopilot/rule-lab/apply', {
    actions,
    author,
    allow_active: allowActive,
  })

export const executeDirectAITrade = (decision: AIDirectTrade) =>
  post<{ mode: string; simulated: boolean; trade: Trade }>('/api/autopilot/direct-trades/execute', decision)

// Performance
export const fetchAutopilotPerformance = (window = 30) =>
  get<AutopilotPerformance>(`/api/autopilot/performance?window=${window}`)

export const fetchAutopilotSourcePerformance = (window = 30) =>
  get<SourcePerformance[]>(`/api/autopilot/performance/sources?window=${window}`)

export const fetchAutopilotRulePerformance = (window = 30) =>
  get<RulePerformanceRow[]>(`/api/autopilot/performance/rules?window=${window}`)

// Interventions
export const fetchAutopilotInterventions = (includeResolved = false) =>
  get<AutopilotIntervention[]>(`/api/autopilot/interventions?include_resolved=${includeResolved}`)

export const acknowledgeAutopilotIntervention = (id: number) =>
  post<{ acknowledged: boolean }>(`/api/autopilot/interventions/${id}/ack`)

export const resolveAutopilotIntervention = (id: number, resolvedBy = 'operator') =>
  post<{ resolved: boolean; resolved_by: string }>(`/api/autopilot/interventions/${id}/resolve`, { resolved_by: resolvedBy })

// Decision Ledger
export const fetchDecisionRuns = (limit = 50, offset = 0) =>
  get<DecisionRun[]>(`/api/autopilot/decision-runs?limit=${limit}&offset=${offset}`)

export const fetchDecisionRun = (runId: string) =>
  get<DecisionRun>(`/api/autopilot/decision-runs/${runId}`)

export const fetchDecisionRunItems = (runId: string) =>
  get<DecisionItem[]>(`/api/autopilot/decision-runs/${runId}/items`)

// Evaluation
export const launchEvaluationReplay = (request: ReplayRequest) =>
  post<EvaluationRun>('/api/autopilot/evaluation/replay', request)

export const fetchEvaluationRuns = (limit = 50, offset = 0) =>
  get<EvaluationRun[]>(`/api/autopilot/evaluation/runs?limit=${limit}&offset=${offset}`)

export const fetchEvaluationRun = (evaluationId: string) =>
  get<EvaluationRun>(`/api/autopilot/evaluation/${evaluationId}`)

export const fetchEvaluationSlices = (evaluationId: string) =>
  get<EvaluationSlice[]>(`/api/autopilot/evaluation/${evaluationId}/slices`)

export const fetchEvaluationCompare = (baselineId: string, candidateId: string) =>
  get<EvaluationCompare>(`/api/autopilot/evaluation/compare?baseline=${baselineId}&candidate=${candidateId}`)

// Circuit Breaker
export interface CircuitBreakerStatus {
  breaker_tripped: boolean
  threshold: number
  counts: Record<string, number>
  last_failure_times: Record<string, string>
}

export const fetchCircuitBreakerStatus = () =>
  get<CircuitBreakerStatus>('/api/autopilot/circuit-breaker')

export const resetCircuitBreaker = () =>
  post<{ ok: boolean; message: string }>('/api/autopilot/circuit-breaker/reset')

// Bull/Bear Debate
export interface DebateResult {
  winner: 'BULL' | 'BEAR' | 'NEUTRAL'
  bull: { conviction: number; thesis: string; key_factors: string[] }
  bear: { conviction: number; thesis: string; key_factors: string[] }
  net_conviction: number
  should_trade: boolean
  debate_rounds: number
}

export const runBullBearDebate = (params: {
  symbol: string
  price?: number
  change_pct?: number
  sector?: string
  technicals?: string
  market_context?: string
}) => post<DebateResult>('/api/autopilot/debate', params)

// Multi-Persona Analysis
export interface PersonaResult {
  score: number
  reasoning: string
  key_insight: string
  weight: number
}

export interface PersonaAnalysisResult {
  symbol: string
  composite_score: number
  verdict: 'BULLISH' | 'BEARISH' | 'NEUTRAL'
  personas: Record<string, PersonaResult>
}

export const runPersonaAnalysis = (params: {
  symbol: string
  price?: number
  sector?: string
  data_summary?: string
}) => post<PersonaAnalysisResult>('/api/autopilot/persona-analysis', params)
