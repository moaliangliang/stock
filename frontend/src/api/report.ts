/**
 * 分析报告相关 API
 */
import request from '@/utils/request'

export const reportApi = {
  list(params?: { keyword?: string; file_type?: string }) {
    return request.get('/reports', { params })
  },

  downloadUrl(filename: string) {
    return `/api/v1/reports/download/${filename}`
  },

  getAlertLog() {
    return request.get('/reports/alert-log')
  },
}
