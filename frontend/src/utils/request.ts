/**
 * Axios HTTP 请求封装
 * - 统一错误处理
 * - 自动携带 JWT Token
 * - 请求/响应拦截器
 */
import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'
import { getToken, clearToken } from '@/utils/token'

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器 - 自动携带 Token
request.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = getToken()
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// 响应拦截器 - 统一错误处理
request.interceptors.response.use(
  (response) => {
    return response.data
  },
  (error: AxiosError<{ detail?: string; message?: string }>) => {
    const status = error.response?.status
    const detail = error.response?.data?.detail || error.response?.data?.message || '请求失败'

    switch (status) {
      case 401:
        clearToken()
        sessionStorage.removeItem('user')
        router.push('/login')
        ElMessage.error('登录已过期，请重新登录')
        break
      case 403:
        ElMessage.error('权限不足')
        break
      case 404:
        ElMessage.warning('请求的资源不存在')
        break
      case 422:
        ElMessage.warning('参数校验失败')
        break
      case 500:
        ElMessage.error('服务器错误')
        break
      default:
        ElMessage.error(detail)
    }
    return Promise.reject(error)
  }
)

export default request
