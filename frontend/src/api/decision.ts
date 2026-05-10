/**
 * 投资决策 API
 */
import request from '@/utils/request'

export const decisionApi = {
  /** 批量生成决策 */
  generate(symbols: string[]) {
    return request.post('/decisions/generate', { symbols })
  },

  /** 查询决策列表 */
  getDecisions(params?: {
    status?: string
    symbol?: string
    page?: number
    page_size?: number
  }) {
    return request.get('/decisions', { params })
  },

  /** 获取仪表盘汇总 */
  getSummary() {
    return request.get('/decisions/summary')
  },

  /** 获取单条决策详情 */
  getDecision(id: number) {
    return request.get(`/decisions/${id}`)
  },

  /** 执行决策 */
  execute(id: number) {
    return request.put(`/decisions/${id}/execute`)
  },

  /** 忽略决策 */
  dismiss(id: number) {
    return request.put(`/decisions/${id}/dismiss`)
  },

  /** 获取决策准确率统计 */
  getOutcomeSummary(days?: number) {
    return request.get('/decisions/outcomes/summary', { params: { days } })
  },
}
