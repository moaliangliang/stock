/**
 * 回测相关 API
 */
import request from '@/utils/request'
import type { ApiResponse } from '@/types/api'

export interface BacktestResult {
  total_return: number
  annual_return: number
  max_drawdown: number
  sharpe_ratio: number
  win_rate: number
  total_trades: number
  profit_trades: number
  loss_trades: number
  profit_factor: number
  final_equity: number
  equity_curve: number[][]
  trades: Array<Record<string, any>>
}

export const backtestApi = {
  run(data: {
    strategy_id: number
    symbol: string
    interval: string
    start_date: string
    end_date: string
    initial_capital?: number
  }) {
    return request.post<unknown, ApiResponse<BacktestResult>>('/backtest/run', data)
  },

  getHistory(params?: { skip?: number; limit?: number; strategy_id?: number }) {
    return request.get<unknown, ApiResponse<Array<Record<string, any>>>>('/backtest/history', { params })
  },
}
