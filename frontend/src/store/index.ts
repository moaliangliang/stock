/**
 * Pinia 状态管理 - 用户认证、全局状态
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { UserInfo } from '@/types/api'
import { getToken, setToken as saveToken, clearToken } from '@/utils/token'

const ROLE_HIERARCHY: Record<string, number> = { admin: 3, trader: 2, viewer: 1 }

function safeJsonParse<T>(raw: string | null, fallback: T): T {
  if (!raw) return fallback
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(getToken())
  const user = ref<UserInfo | null>(safeJsonParse<UserInfo | null>(sessionStorage.getItem('user'), null))

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const username = computed(() => user.value?.username || '')
  const userRole = computed(() => user.value?.role || 'viewer')

  function hasRole(minRole: string): boolean {
    return (ROLE_HIERARCHY[userRole.value] || 0) >= (ROLE_HIERARCHY[minRole] || 0)
  }

  function setToken(newToken: string) {
    token.value = newToken
    saveToken(newToken)
  }

  function setUser(newUser: UserInfo) {
    user.value = newUser
    sessionStorage.setItem('user', JSON.stringify(newUser))
  }

  function logout() {
    token.value = ''
    user.value = null
    clearToken()
    sessionStorage.removeItem('user')
  }

  return { token, user, isLoggedIn, isAdmin, username, userRole, hasRole, setToken, setUser, logout }
})

export type ThemeMode = 'light' | 'dark' | 'auto'

// App level store
export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = ref(false)
  const theme = ref<ThemeMode>((localStorage.getItem('app-theme') as ThemeMode) || 'auto')
  const routeLoading = ref(false)

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function setTheme(mode: ThemeMode) {
    theme.value = mode
    localStorage.setItem('app-theme', mode)
  }

  return { sidebarCollapsed, theme, routeLoading, toggleSidebar, setTheme }
})
