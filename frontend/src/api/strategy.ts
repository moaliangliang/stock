/**
 * 策略相关 API
 */
import request from '@/utils/request'

export const strategyApi = {
  getStrategies(params?: { skip?: number; limit?: number; status?: string }) {
    return request.get('/strategies', { params })
  },

  getStrategy(id: number) {
    return request.get(`/strategies/${id}`)
  },

  createStrategy(data: any) {
    return request.post('/strategies', data)
  },

  updateStrategy(id: number, data: any) {
    return request.put(`/strategies/${id}`, data)
  },

  deleteStrategy(id: number) {
    return request.delete(`/strategies/${id}`)
  },

  runStrategy(id: number) {
    return request.post(`/strategies/${id}/run`)
  },

  getStrategyLogs(id: number, params?: { skip?: number; limit?: number }) {
    return request.get(`/strategies/${id}/logs`, { params })
  },

  getTemplates() {
    return request.get('/strategies/templates')
  },

  getClassicStrategies() {
    return request.get('/strategies/classic')
  },

  runRegressionTest() {
    return request.post('/strategies/regression-test')
  },
}
