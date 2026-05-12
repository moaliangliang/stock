/**
 * 认证相关 API
 */
import request from '@/utils/request'
import type { ApiResponse, UserInfo, TokenData } from '@/types/api'

export const authApi = {
  login(username: string, password: string) {
    const params = new URLSearchParams()
    params.append('username', username)
    params.append('password', password)
    return request.post<unknown, ApiResponse<TokenData>>('/auth/login', params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
  },

  register(data: { username: string; email: string; password: string }) {
    return request.post<unknown, ApiResponse<UserInfo>>('/auth/register', data)
  },

  getProfile() {
    return request.get<unknown, ApiResponse<UserInfo>>('/auth/me')
  },

  updateProfile(data: Partial<UserInfo>) {
    return request.put<unknown, ApiResponse<UserInfo>>('/auth/me', data)
  },

  getUsers(params?: { skip?: number; limit?: number }) {
    return request.get<unknown, ApiResponse<UserInfo[]>>('/auth/users', { params })
  },

  changePassword(data: { current_password: string; new_password: string }) {
    return request.post<unknown, ApiResponse<null>>('/auth/change-password', data)
  },
}
