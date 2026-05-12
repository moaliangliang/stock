/**
 * 交易相关 API
 */
import request from '@/utils/request'
import type { ApiResponse, Order, Position, Trade } from '@/types/api'

export const tradeApi = {
  createOrder(data: {
    symbol: string
    side: string
    type: string
    price?: number
    quantity: number
    strategy_id?: number
  }) {
    return request.post<unknown, ApiResponse<Order>>('/trade/orders', data)
  },

  cancelOrder(orderId: number) {
    return request.post<unknown, ApiResponse<Order>>(`/trade/orders/${orderId}/cancel`)
  },

  getOrders(params?: {
    status?: string
    symbol?: string
    skip?: number
    limit?: number
  }) {
    return request.get<unknown, ApiResponse<Order[]>>('/trade/orders', { params })
  },

  getPositions() {
    return request.get<unknown, ApiResponse<Position[]>>('/trade/positions')
  },

  getTrades(params?: { skip?: number; limit?: number }) {
    return request.get<unknown, ApiResponse<Trade[]>>('/trade/trades', { params })
  },

  createPosition(data: {
    symbol: string
    quantity: number
    cost_price: number
    leverage?: number
  }) {
    return request.post<unknown, ApiResponse<Position>>('/trade/positions', data)
  },

  importPositions(file: File) {
    const formData = new FormData()
    formData.append('file', file)
    return request.post<unknown, ApiResponse<{ imported: number }>>('/trade/positions/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  syncPositions() {
    return request.post<unknown, ApiResponse<{
      created: number
      updated: number
      total: number
      positions: Array<{ symbol: string; quantity: number; cost_price: number; market_value: number }>
    }>>('/trade/positions/sync')
  },
}
