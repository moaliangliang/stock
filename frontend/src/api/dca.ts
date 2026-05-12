/**
 * DCA 定投回测 API
 */
import request from '@/utils/request'
import type { ApiResponse } from '@/types/api'

export interface DcaBacktestParams {
  symbols?: string[]
  start_date: string
  end_date: string
  amount: number
  mode: 'fixed' | 'smart'
  smart_aggressiveness?: number
  smart_min_multiplier?: number
  smart_max_multiplier?: number
}

export const dcaApi = {
  /** 获取支持的定投标的 */
  getIndices() {
    return request.get<unknown, ApiResponse<Array<{ symbol: string; name: string }>>>('/dca/indices')
  },

  /** 执行 DCA 回测 */
  backtest(params: DcaBacktestParams) {
    return request.post<unknown, ApiResponse<Record<string, any>>>('/dca/backtest', params)
  },
}
