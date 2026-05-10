/**
 * 交易相关 API
 */
import request from '@/utils/request'

export const tradeApi = {
  createOrder(data: {
    symbol: string
    side: string
    type: string
    price?: number
    quantity: number
    strategy_id?: number
  }) {
    return request.post('/trade/orders', data)
  },

  cancelOrder(orderId: number) {
    return request.post(`/trade/orders/${orderId}/cancel`)
  },

  getOrders(params?: {
    status?: string
    symbol?: string
    skip?: number
    limit?: number
  }) {
    return request.get('/trade/orders', { params })
  },

  getPositions() {
    return request.get('/trade/positions')
  },

  getTrades(params?: { skip?: number; limit?: number }) {
    return request.get('/trade/trades', { params })
  },

  createPosition(data: {
    symbol: string
    quantity: number
    cost_price: number
    leverage?: number
  }) {
    return request.post('/trade/positions', data)
  },

  importPositions(file: File) {
    const formData = new FormData()
    formData.append('file', file)
    return request.post('/trade/positions/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}
