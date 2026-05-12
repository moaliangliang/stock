/**
 * 风控相关 API
 */
import request from '@/utils/request'
import type { ApiResponse } from '@/types/api'

export interface RiskRule {
  id: number
  name: string
  rule_type: string
  action: string
  is_active: boolean
  params: Record<string, any> | null
  symbols: string | null
  description: string | null
}

export interface RiskRecord {
  id: number
  rule_id: number | null
  user_id: number
  symbol: string | null
  action: string
  trigger_value: number | null
  limit_value: number | null
  message: string | null
  created_at: string
}

export const riskApi = {
  getRules() {
    return request.get<unknown, ApiResponse<RiskRule[]>>('/risk/rules')
  },

  createRule(data: Partial<RiskRule>) {
    return request.post<unknown, ApiResponse<RiskRule>>('/risk/rules', data)
  },

  updateRule(id: number, data: Partial<RiskRule>) {
    return request.put<unknown, ApiResponse<RiskRule>>(`/risk/rules/${id}`, data)
  },

  deleteRule(id: number) {
    return request.delete<unknown, ApiResponse<null>>(`/risk/rules/${id}`)
  },

  getRecords(params?: { skip?: number; limit?: number }) {
    return request.get<unknown, ApiResponse<RiskRecord[]>>('/risk/records', { params })
  },
}
