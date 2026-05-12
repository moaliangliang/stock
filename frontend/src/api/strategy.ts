/**
 * 策略相关 API
 */
import request from '@/utils/request'
import type { ApiResponse } from '@/types/api'

export interface StrategyData {
  id: number
  name: string
  type: string
  symbol?: string
  interval?: string
  params: Record<string, any>
  status: string
  is_active: boolean
  created_at: string
}

export const strategyApi = {
  getStrategies(params?: { skip?: number; limit?: number; status?: string }) {
    return request.get<unknown, ApiResponse<StrategyData[]>>('/strategies', { params })
  },

  getStrategy(id: number) {
    return request.get<unknown, ApiResponse<StrategyData>>(`/strategies/${id}`)
  },

  createStrategy(data: Partial<StrategyData>) {
    return request.post<unknown, ApiResponse<StrategyData>>('/strategies', data)
  },

  updateStrategy(id: number, data: Partial<StrategyData>) {
    return request.put<unknown, ApiResponse<StrategyData>>(`/strategies/${id}`, data)
  },

  deleteStrategy(id: number) {
    return request.delete<unknown, ApiResponse<null>>(`/strategies/${id}`)
  },

  runStrategy(id: number) {
    return request.post<unknown, ApiResponse<{ signals: Array<Record<string, any>> }>>(`/strategies/${id}/run`)
  },

  getStrategyLogs(id: number, params?: { skip?: number; limit?: number }) {
    return request.get<unknown, ApiResponse<Array<Record<string, any>>>>(`/strategies/${id}/logs`, { params })
  },

  getTemplates() {
    return request.get<unknown, ApiResponse<Array<Record<string, any>>>>('/strategies/templates')
  },

  getClassicStrategies() {
    return request.get<unknown, ApiResponse<Array<Record<string, any>>>>('/strategies/classic')
  },

  runRegressionTest() {
    return request.post<unknown, ApiResponse<Record<string, any>>>('/strategies/regression-test')
  },
}
