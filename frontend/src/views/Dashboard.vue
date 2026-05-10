<template>
  <div>
    <h3 style="margin-bottom: 20px">📊 仪表盘</h3>

    <!-- 统计卡片 -->
    <el-row :gutter="16">
      <el-col :span="6" v-for="stat in stats" :key="stat.label">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value" :style="{ color: stat.color }">{{ stat.value }}</div>
          <div class="stat-label">{{ stat.label }}</div>
          <div v-if="stat.change !== undefined" class="stat-change" :class="stat.change >= 0 ? 'price-up' : 'price-down'">
            {{ stat.change >= 0 ? '↑' : '↓' }} {{ Math.abs(stat.change) }}%
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 运营概览 -->
    <el-row :gutter="16">
      <el-col :span="16">
        <el-card shadow="hover">
          <template #header><span>📈 收益曲线</span></template>
          <div ref="chartRef" style="height: 350px"></div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header><span>⚡ 快速操作</span></template>
          <div class="quick-actions">
            <el-button type="primary" @click="$router.push('/market')">查看行情</el-button>
            <el-button type="success" @click="$router.push('/strategy')">创建策略</el-button>
            <el-button type="warning" @click="$router.push('/backtest')">运行回测</el-button>
            <el-button type="danger" @click="$router.push('/trade')">实盘交易</el-button>
          </div>
        </el-card>
        <el-card shadow="hover" style="margin-top: 16px">
          <template #header><span>📋 系统状态</span></template>
          <div class="system-status">
            <div class="status-item">
              <span>后端服务</span>
              <el-tag :type="systemStatus.backend ? 'success' : 'danger'" size="small">
                {{ systemStatus.backend ? '运行中' : '离线' }}
              </el-tag>
            </div>
            <div class="status-item">
              <span>数据库</span>
              <el-tag :type="systemStatus.database ? 'success' : 'danger'" size="small">
                {{ systemStatus.database ? '已连接' : '离线' }}
              </el-tag>
            </div>
            <div class="status-item">
              <span>活跃策略</span>
              <el-tag type="info" size="small">{{ systemStatus.activeStrategies }} 个</el-tag>
            </div>
            <div class="status-item">
              <span>当前持仓</span>
              <el-tag type="info" size="small">{{ systemStatus.positionCount }} 个</el-tag>
            </div>
            <div class="status-item token-usage">
              <span>Skills API</span>
              <div class="token-info">
                <span class="token-text">{{ tokenStats.callCount }}</span>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { tradeApi } from '@/api'
import { strategyApi } from '@/api'
import request from '@/utils/request'

const stats = ref([
  { label: '账户总资产', value: '¥ --', color: '#1890ff' },
  { label: '今日盈亏', value: '--', color: '#52c41a' },
  { label: '持仓数量', value: '--', color: '#faad14' },
  { label: '运行策略', value: '--', color: '#722ed1' },
])

const systemStatus = reactive({
  backend: false,
  database: false,
  activeStrategies: 0,
  positionCount: 0,
})

const tokenStats = reactive({
  callCount: 0,
})

const chartRef = ref<HTMLElement>()
let chart: any = null
let resizeHandler: (() => void) | null = null

async function loadDashboard() {
  try {
    const [healthRes, posRes, stratRes, tokenRes] = await Promise.allSettled([
      request.get('/health'),
      tradeApi.getPositions(),
      strategyApi.getStrategies({ limit: 100 }),
      request.get('/system/token-stats'),
    ])

    if (healthRes.status === 'fulfilled') {
      const data = (healthRes.value as any)?.data
      systemStatus.backend = true
      systemStatus.database = data?.status === 'running'
    }

    if (posRes.status === 'fulfilled') {
      const positions = (posRes.value as any)?.data || []
      systemStatus.positionCount = positions.length
      stats.value[2] = { ...stats.value[2], value: String(positions.length) }
    }

    if (stratRes.status === 'fulfilled') {
      const strategies = (stratRes.value as any)?.data || []
      const active = strategies.filter((s: any) => s.status === 'active').length
      systemStatus.activeStrategies = active
      stats.value[3] = { ...stats.value[3], value: String(active) }
    }

    if (tokenRes.status === 'fulfilled') {
      const data = (tokenRes.value as any)?.data || {}
      const skills = Array.isArray(data) ? data.find((d: any) => d.api_name === 'skills_api') : data
      if (skills) {
        tokenStats.callCount = skills.call_count || 0
      }
    }
  } catch (err) {
    console.error('Dashboard data load failed', err)
  }
}

async function initChart() {
  if (!chartRef.value) return

  const echarts = (await import('echarts')).default
  chart = echarts.init(chartRef.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'category',
      data: ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'],
      boundaryGap: false,
    },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
    series: [{
      name: '收益率',
      type: 'line',
      smooth: true,
      data: [2.5, 3.8, 5.2, 4.6, 7.1, 8.5, 10.2, 12.5, 11.8, 13.2, 15.1, 18.5],
      areaStyle: { opacity: 0.3 },
      itemStyle: { color: '#1890ff' },
    }],
  })

  resizeHandler = () => chart?.resize()
  window.addEventListener('resize', resizeHandler)
}

onMounted(async () => {
  await loadDashboard()
  initChart()
})

onUnmounted(() => {
  if (resizeHandler) {
    window.removeEventListener('resize', resizeHandler)
    resizeHandler = null
  }
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.stat-card {
  text-align: center;
}
.stat-value {
  font-size: 28px;
  font-weight: bold;
}
.stat-label {
  color: #999;
  font-size: 14px;
  margin: 8px 0;
}
.stat-change {
  font-size: 13px;
}
.quick-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.quick-actions .el-button {
  flex: 0 0 calc(50% - 5px);
  height: 36px;
  justify-content: center;
}
.system-status {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.status-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
}
.token-usage {
  flex-wrap: wrap;
}
.token-info {
  display: flex;
  align-items: center;
  gap: 8px;
}
.token-text {
  font-size: 12px;
  color: #666;
  white-space: nowrap;
}

</style>
