/**
 * 回测相关 API
 */
import request from '@/utils/request'

export const backtestApi = {
  run(data: {
    strategy_id: number
    symbol: string
    interval: string
    start_date: string
    end_date: string
    initial_capital?: number
  }) {
    return request.post('/backtest/run', data)
  },

  getHistory(params?: { skip?: number; limit?: number; strategy_id?: number }) {
    return request.get('/backtest/history', { params })
  },
}
