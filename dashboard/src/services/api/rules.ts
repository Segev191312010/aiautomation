import type { Rule, RuleCreate, RuleTemplate } from '@/types'
import { get, post, put, del } from './client'

export const fetchRules         = () => get<Rule[]>('/api/rules')
export const fetchRule          = (id: string) => get<Rule>(`/api/rules/${id}`)
export const createRule         = (body: RuleCreate) => post<Rule>('/api/rules', body)
export const updateRule         = (id: string, body: Partial<Rule>) => put<Rule>(`/api/rules/${id}`, body)
export const deleteRule         = (id: string) => del<{ deleted: boolean }>(`/api/rules/${id}`)
export const toggleRule         = (id: string) => post<{ id: string; enabled: boolean }>(`/api/rules/${id}/toggle`)
export const fetchRuleTemplates = () => get<RuleTemplate[]>('/api/rules/templates')
