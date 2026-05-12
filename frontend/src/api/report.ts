/**
 * 分析报告相关 API
 */
import request from '@/utils/request'
import type { ApiResponse } from '@/types/api'

export interface ReportItem {
  name: string
  size: number
  size_display: string
  modified: string
  type: string
  download_url: string
}

export interface AlertLogData {
  content: string
  updated: string | null
  current_prices: Record<string, any> | null
}

export const reportApi = {
  list(params?: { keyword?: string; file_type?: string }) {
    return request.get<unknown, ApiResponse<ReportItem[]>>('/reports', { params })
  },

  downloadUrl(filename: string) {
    return `/api/v1/reports/download/${filename}`
  },

  getAlertLog() {
    return request.get<unknown, ApiResponse<AlertLogData>>('/reports/alert-log')
  },
}
