/**
 * 通知相关 API
 */
import request from '@/utils/request'

export const notificationApi = {
  getNotifications(params?: { skip?: number; limit?: number; unread_only?: boolean }) {
    return request.get('/notifications', { params })
  },

  getUnreadCount() {
    return request.get('/notifications/unread-count')
  },

  markRead(id: number) {
    return request.put(`/notifications/${id}/read`)
  },

  markAllRead() {
    return request.put('/notifications/read-all')
  },
}
