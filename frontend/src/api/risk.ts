/**
 * 风控相关 API
 */
import request from '@/utils/request'

export const riskApi = {
  getRules() {
    return request.get('/risk/rules')
  },

  createRule(data: any) {
    return request.post('/risk/rules', data)
  },

  updateRule(id: number, data: any) {
    return request.put(`/risk/rules/${id}`, data)
  },

  deleteRule(id: number) {
    return request.delete(`/risk/rules/${id}`)
  },

  getRecords(params?: { skip?: number; limit?: number }) {
    return request.get('/risk/records', { params })
  },
}
