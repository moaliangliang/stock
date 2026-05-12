/**
 * Token Manager — 闭包存储 JWT，sessionStorage 持久化
 * 避免 XSS 直接读取 window.localStorage.token
 */

const STORAGE_KEY = 'session_token'

let _token: string = sessionStorage.getItem(STORAGE_KEY) || ''

export function getToken(): string {
  return _token
}

export function setToken(token: string): void {
  _token = token
  if (token) {
    sessionStorage.setItem(STORAGE_KEY, token)
  } else {
    sessionStorage.removeItem(STORAGE_KEY)
  }
}

export function clearToken(): void {
  _token = ''
  sessionStorage.removeItem(STORAGE_KEY)
}

export function hasToken(): boolean {
  return _token.length > 0
}
