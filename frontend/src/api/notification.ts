/**
 * 通知相关 API
 */
import request from '@/utils/request'
import type { ApiResponse } from '@/types/api'

export interface NotificationItem {
  id: number
  title: string
  content: string
  type: string
  is_read: boolean
  created_at: string
}

export const notificationApi = {
  getNotifications(params?: { skip?: number; limit?: number; unread_only?: boolean }) {
    return request.get<unknown, ApiResponse<NotificationItem[]>>('/notifications', { params })
  },

  getUnreadCount() {
    return request.get<unknown, ApiResponse<{ count: number }>>('/notifications/unread-count')
  },

  markRead(id: number) {
    return request.put<unknown, ApiResponse<null>>(`/notifications/${id}/read`)
  },

  markAllRead() {
    return request.put<unknown, ApiResponse<null>>('/notifications/read-all')
  },
}
