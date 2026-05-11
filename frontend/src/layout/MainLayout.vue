<template>
  <el-container style="height: 100vh">
    <!-- 侧边栏 -->
    <el-aside :width="isCollapsed ? '64px' : '220px'" class="app-aside">
      <div class="logo">
        <span v-if="!isCollapsed" class="logo-text">📊 量化交易平台</span>
        <span v-else class="logo-text">📊</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapsed"
        :router="true"
        background-color="#001529"
        text-color="#ffffffcc"
        active-text-color="#1890ff"
      >
        <el-menu-item index="/dashboard">
          <el-icon><Monitor /></el-icon>
          <span>仪表盘</span>
        </el-menu-item>
        <el-menu-item index="/market">
          <el-icon><DataLine /></el-icon>
          <span>行情中心</span>
        </el-menu-item>
        <el-menu-item index="/strategy">
          <el-icon><SetUp /></el-icon>
          <span>策略中心</span>
        </el-menu-item>
        <el-menu-item index="/backtest">
          <el-icon><Histogram /></el-icon>
          <span>回测系统</span>
        </el-menu-item>
        <el-menu-item index="/trade">
          <el-icon><Money /></el-icon>
          <span>实盘交易</span>
        </el-menu-item>
        <el-menu-item index="/risk">
          <el-icon><Warning /></el-icon>
          <span>风控管理</span>
        </el-menu-item>
        <el-menu-item index="/dca">
          <el-icon><TrendCharts /></el-icon>
          <span>定投回测</span>
        </el-menu-item>
        <el-menu-item index="/decision">
          <el-icon><Opportunity /></el-icon>
          <span>投资决策</span>
        </el-menu-item>
        <el-menu-item index="/reports">
          <el-icon><Document /></el-icon>
          <span>分析报告</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- 主区域 -->
    <el-container>
      <!-- 顶部导航栏 -->
      <el-header class="app-header">
        <div class="header-left">
          <el-icon class="collapse-btn" @click="toggleCollapse" style="cursor: pointer; font-size: 20px">
            <Fold v-if="!isCollapsed" />
            <Expand v-else />
          </el-icon>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="currentTitle">{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <!-- 通知 -->
          <NotificationBell />

          <!-- 主题切换 -->
          <el-tooltip :content="themeLabel" placement="bottom">
            <el-button size="small" circle @click="cycleTheme" style="margin-right: 12px">
              <el-icon><Moon v-if="resolvedTheme === 'dark'" /><Sunny v-else /></el-icon>
            </el-button>
          </el-tooltip>

          <el-dropdown trigger="click">
            <span class="user-info">
              <el-avatar :size="32" icon="UserFilled" />
              <span style="margin-left: 8px">{{ auth.username }}</span>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="changePasswordDialog?.open()">
                  <el-icon><Lock /></el-icon>修改密码
                </el-dropdown-item>
                <el-dropdown-item divided @click="handleLogout">
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 内容区域 -->
      <el-main class="app-main">
        <div v-if="appStore.routeLoading" class="route-loading-bar" />
        <router-view />
      </el-main>
    </el-container>
  </el-container>

  <!-- 修改密码对话框 -->
  <ChangePasswordDialog ref="changePasswordDialog" />
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore, useAppStore } from '@/store'
import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue'
import NotificationBell from '@/components/NotificationBell.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const appStore = useAppStore()

const changePasswordDialog = ref<InstanceType<typeof ChangePasswordDialog>>()

const isCollapsed = ref(false)
const toggleCollapse = () => (isCollapsed.value = !isCollapsed.value)

const activeMenu = computed(() => route.path)
const currentTitle = computed(() => route.meta?.title as string)

const resolvedTheme = computed(() => {
  if (appStore.theme !== 'auto') return appStore.theme
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
})

const themeLabel = computed(() => {
  if (appStore.theme === 'auto') return resolvedTheme.value === 'dark' ? '自动(暗色)' : '自动(亮色)'
  return appStore.theme === 'dark' ? '暗色模式' : '亮色模式'
})

function cycleTheme() {
  const modes: Array<'light' | 'dark' | 'auto'> = ['light', 'dark', 'auto']
  const idx = modes.indexOf(appStore.theme)
  appStore.setTheme(modes[(idx + 1) % modes.length])
}

function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.app-aside {
  background-color: #001529;
  overflow-y: auto;
  transition: width 0.3s;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 16px;
  font-weight: bold;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}
.app-header {
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  z-index: 10;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.header-right {
  display: flex;
  align-items: center;
}
.user-info {
  display: flex;
  align-items: center;
  cursor: pointer;
}
.app-main {
  background: #f0f2f5;
  min-height: calc(100vh - 60px);
}
.route-loading-bar {
  height: 2px;
  background: linear-gradient(90deg, #1890ff, #52c41a, #1890ff);
  background-size: 200% 100%;
  animation: loadingSlide 1.5s ease infinite;
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 999;
}
@keyframes loadingSlide {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
