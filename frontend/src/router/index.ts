/**
 * Vue Router 路由配置
 */
import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useAuthStore, useAppStore } from '@/store'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/views/Register.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/',
    component: () => import('@/layout/MainLayout.vue'),
    redirect: '/dashboard',
    meta: { requiresAuth: true },
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '仪表盘', icon: 'Monitor' },
      },
      {
        path: 'market',
        name: 'Market',
        component: () => import('@/views/Market.vue'),
        meta: { title: '行情中心', icon: 'DataLine' },
      },
      {
        path: 'strategy',
        name: 'Strategy',
        component: () => import('@/views/Strategy.vue'),
        meta: { title: '策略中心', icon: 'SetUp' },
      },
      {
        path: 'backtest',
        name: 'Backtest',
        component: () => import('@/views/Backtest.vue'),
        meta: { title: '回测系统', icon: 'Histogram' },
      },
      {
        path: 'trade',
        name: 'Trade',
        component: () => import('@/views/Trade.vue'),
        meta: { title: '实盘交易', icon: 'Money' },
      },
      {
        path: 'risk',
        name: 'Risk',
        component: () => import('@/views/Risk.vue'),
        meta: { title: '风控管理', icon: 'Warning' },
      },
      {
        path: 'decision',
        name: 'Decision',
        component: () => import('@/views/Decision.vue'),
        meta: { title: '投资决策', icon: 'Opportunity' },
      },
      {
        path: 'dca',
        name: 'DcaBacktest',
        component: () => import('@/views/DcaBacktest.vue'),
        meta: { title: '定投回测', icon: 'TrendCharts' },
      },
      {
        path: 'reports',
        name: 'Reports',
        component: () => import('@/views/Report.vue'),
        meta: { title: '分析报告', icon: 'Document' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫 - 认证检查
router.beforeEach((to, _from, next) => {
  const auth = useAuthStore()
  const app = useAppStore()
  app.routeLoading = true
  if (to.meta.requiresAuth !== false && !auth.isLoggedIn) {
    next('/login')
  } else if (to.path === '/login' && auth.isLoggedIn) {
    next('/dashboard')
  } else {
    next()
  }
})

router.afterEach(() => {
  setTimeout(() => {
    const app = useAppStore()
    app.routeLoading = false
  }, 300)
})

export default router
