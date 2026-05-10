/**
 * 认证相关 API
 */
import request from '@/utils/request'

export const authApi = {
  login(username: string, password: string) {
    const params = new URLSearchParams()
    params.append('username', username)
    params.append('password', password)
    return request.post('/auth/login', params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
  },

  register(data: { username: string; email: string; password: string }) {
    return request.post('/auth/register', data)
  },

  getProfile() {
    return request.get('/auth/me')
  },

  updateProfile(data: any) {
    return request.put('/auth/me', data)
  },

  getUsers(params?: { skip?: number; limit?: number }) {
    return request.get('/auth/users', { params })
  },

  changePassword(data: { current_password: string; new_password: string }) {
    return request.post('/auth/change-password', data)
  },
}
