<template>
  <div>
    <h3 style="margin-bottom: 20px">策略中心</h3>

    <el-tabs v-model="activeTab" type="border-card">
      <!-- ========== 我的策略 ========== -->
      <el-tab-pane label="我的策略" name="mine">
        <el-card shadow="hover" style="margin-bottom: 16px">
          <el-button type="primary" @click="showCreateDialog">
            <el-icon><Plus /></el-icon> 新建策略
          </el-button>
        </el-card>

        <el-card shadow="hover">
          <el-table :data="strategies" stripe v-loading="loadingStrategies">
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="name" label="策略名称" min-width="150" />
            <el-table-column prop="type" label="类型" width="110">
              <template #default="{ row }">
                <el-tag>{{ typeLabel(row.type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="symbols" label="标的" width="180">
              <template #default="{ row }">
                {{ row.symbols?.join(', ') }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="280" fixed="right">
              <template #default="{ row }">
                <el-button size="small" @click="editStrategy(row)">编辑</el-button>
                <el-button size="small" type="primary" @click="runStrategy(row)" :loading="runningId === row.id">
                  运行
                </el-button>
                <el-button
                  size="small"
                  :type="row.status === 'active' ? 'warning' : 'success'"
                  @click="toggleStatus(row)"
                >
                  {{ row.status === 'active' ? '暂停' : '启用' }}
                </el-button>
                <el-popconfirm title="确认删除?" @confirm="deleteStrategy(row.id)">
                  <template #reference>
                    <el-button size="small" type="danger">删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ========== 经典策略 ========== -->
      <el-tab-pane label="经典策略" name="classic">
        <el-card shadow="hover" style="margin-bottom: 16px">
          <div style="color: #909399; font-size: 14px">
            内置经过市场验证的经典量化策略模板，点击「创建策略」可基于模板快速创建自己的策略。
          </div>
        </el-card>

        <div v-loading="loadingClassic">
          <el-row :gutter="16">
            <el-col
              v-for="item in classicStrategies"
              :key="item.type"
              :xs="24" :sm="12" :md="8"
              style="margin-bottom: 16px"
            >
              <el-card shadow="hover" class="classic-card">
                <div class="classic-header">
                  <div>
                    <span class="classic-name">{{ item.name }}</span>
                    <el-tag size="small" type="info" style="margin-left: 8px">{{ item.type }}</el-tag>
                  </div>
                  <el-tag
                    size="small"
                    :type="item.suitable_market === '趋势市' ? 'danger' : item.suitable_market === '震荡市' ? 'warning' : 'success'"
                  >
                    {{ item.suitable_market }}
                  </el-tag>
                </div>
                <p class="classic-desc">{{ item.description }}</p>

                <el-collapse>
                  <el-collapse-item title="参数说明" name="params">
                    <div v-for="(desc, key) in item.params_description" :key="key" class="param-item">
                      <code>{{ key }}</code>: {{ desc }}
                    </div>
                  </el-collapse-item>
                  <el-collapse-item title="历史表现参考" name="perf">
                    <div v-for="(val, key) in item.performance_metrics" :key="key" class="param-item">
                      <code>{{ key }}</code>: {{ val }}
                    </div>
                  </el-collapse-item>
                </el-collapse>

                <div style="margin-top: 12px; text-align: right">
                  <el-button type="primary" size="small" @click="createFromClassic(item)">
                    创建策略
                  </el-button>
                </div>
              </el-card>
            </el-col>
          </el-row>
        </div>
      </el-tab-pane>

      <!-- ========== 回归测试 ========== -->
      <el-tab-pane label="回归测试" name="regression">
        <el-card shadow="hover" style="margin-bottom: 16px">
          <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px">
            <div style="color: #909399; font-size: 14px">
              对所有内置经典策略运行回归测试，使用标准模拟数据集（1年60分钟K线），验证策略引擎运行正常并比较各策略表现。
            </div>
            <el-button
              type="primary"
              @click="runRegressionTest"
              :loading="testing"
              :disabled="testing"
            >
              {{ testing ? '测试中...' : '运行回归测试' }}
            </el-button>
          </div>
        </el-card>

        <!-- 测试结果 -->
        <template v-if="regressionResult">
          <el-card shadow="hover" style="margin-bottom: 16px">
            <div style="display: flex; gap: 24px; flex-wrap: wrap">
              <div class="stat-item">
                <div class="stat-value">{{ regressionResult.test_dataset?.total_bars || 0 }}</div>
                <div class="stat-label">测试K线数</div>
              </div>
              <div class="stat-item">
                <div class="stat-value" style="color: #67c23a">{{ regressionResult.summary?.passed || 0 }}</div>
                <div class="stat-label">通过</div>
              </div>
              <div class="stat-item">
                <div class="stat-value" style="color: #f56c6c">{{ regressionResult.summary?.failed || 0 }}</div>
                <div class="stat-label">失败</div>
              </div>
              <div class="stat-item">
                <div class="stat-value">{{ regressionResult.summary?.total || 0 }}</div>
                <div class="stat-label">总计</div>
              </div>
            </div>
          </el-card>

          <el-card shadow="hover">
            <el-table :data="regressionResult.results || []" stripe>
              <el-table-column label="策略名称" prop="name" min-width="120" />
              <el-table-column label="类型" prop="type" width="110" />
              <el-table-column label="状态" prop="status" width="80">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">
                    {{ row.status === 'success' ? '通过' : '失败' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="买入信号" prop="buy_signals" width="100" align="center" />
              <el-table-column label="卖出信号" prop="sell_signals" width="100" align="center" />
              <el-table-column label="总信号数" prop="total_signals" width="100" align="center" />
              <el-table-column label="分析K线" prop="bars_analyzed" width="100" align="center" />
              <el-table-column label="耗时(ms)" prop="duration_ms" width="100" align="center" />
              <el-table-column label="错误信息" prop="error" min-width="150">
                <template #default="{ row }">
                  <span style="color: #f56c6c">{{ row.error }}</span>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </template>

        <el-card v-else shadow="hover">
          <el-empty description="尚未运行回归测试，点击上方按钮开始测试" />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 创建/编辑策略对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEditing ? '编辑策略' : '新建策略'"
      width="700px"
    >
      <el-form :model="form" label-width="100px" size="small">
        <el-form-item label="策略名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="策略类型">
          <el-select v-model="form.type" style="width: 100%">
            <el-option label="均线交叉" value="ma_cross" />
            <el-option label="MACD" value="macd" />
            <el-option label="KDJ" value="kdj" />
            <el-option label="布林带" value="bollinger" />
            <el-option label="网格交易" value="grid" />
            <el-option label="马丁格尔" value="martingale" />
            <el-option label="趋势突破" value="trend_break" />
            <el-option label="自定义" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item label="交易标的">
          <el-select v-model="form.symbols" multiple filterable style="width: 100%">
            <el-option v-for="s in symbolOptions" :key="s.symbol" :label="`${s.name} ${s.symbol}`" :value="s.symbol">
              <div>{{ s.name }}</div>
              <div style="font-size:11px;color:#999">({{ s.symbol }})</div>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="时间周期">
          <el-select v-model="form.intervals" multiple style="width: 100%">
            <el-option label="1分钟" value="1m" />
            <el-option label="5分钟" value="5m" />
            <el-option label="15分钟" value="15m" />
            <el-option label="30分钟" value="30m" />
            <el-option label="60分钟" value="60m" />
            <el-option label="日线" value="1d" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始资金">
          <el-input-number v-model="form.initial_capital" :min="1000" :step="1000" />
        </el-form-item>
        <el-form-item label="策略参数">
          <el-input
            v-model="form.params"
            type="textarea"
            :rows="4"
            placeholder='{"fast_period": 5, "slow_period": 20}'
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveStrategy" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { strategyApi, marketApi } from '@/api'

const activeTab = ref('mine')

// 我的策略
const loadingStrategies = ref(false)
const saving = ref(false)
const runningId = ref<number | null>(null)
const strategies = ref<any[]>([])
const symbolOptions = ref<any[]>([])
const dialogVisible = ref(false)
const isEditing = ref(false)

const form = reactive<any>({
  name: '',
  type: 'ma_cross',
  symbols: [],
  intervals: ['1d'],
  initial_capital: 10000,
  params: '{}',
  description: '',
})

// 经典策略
const loadingClassic = ref(false)
const classicStrategies = ref<any[]>([])

// 回归测试
const testing = ref(false)
const regressionResult = ref<any>(null)

const typeLabel = (t: string) => ({ ma_cross: '均线交叉', macd: 'MACD', kdj: 'KDJ', bollinger: '布林带', grid: '网格', martingale: '马丁格尔', trend_break: '趋势突破', custom: '自定义' }[t] || t)
const statusLabel = (s: string) => ({ draft: '草稿', active: '运行中', paused: '已暂停', stopped: '已停止', error: '异常' }[s] || s)
const statusType = (s: string) => ({ draft: 'info', active: 'success', paused: 'warning', stopped: 'danger', error: 'danger' }[s] || 'info')

async function loadStrategies() {
  loadingStrategies.value = true
  try {
    const res: any = await strategyApi.getStrategies()
    strategies.value = res.data || []
  } finally {
    loadingStrategies.value = false
  }
}

async function loadClassicStrategies() {
  loadingClassic.value = true
  try {
    const res: any = await strategyApi.getClassicStrategies()
    classicStrategies.value = res.data || []
  } finally {
    loadingClassic.value = false
  }
}

async function loadSymbols() {
  try {
    const res: any = await marketApi.getSymbols()
    symbolOptions.value = res.data || []
  } catch (err) { console.error('Operation failed:', err); }
}

function showCreateDialog() {
  isEditing.value = false
  Object.assign(form, { name: '', type: 'ma_cross', symbols: [], intervals: ['1d'], initial_capital: 10000, params: '{}', description: '' })
  dialogVisible.value = true
}

function createFromClassic(item: any) {
  isEditing.value = false
  Object.assign(form, {
    name: item.name + '策略',
    type: item.type,
    symbols: [],
    intervals: ['1d'],
    initial_capital: 10000,
    params: JSON.stringify(item.default_params || {}, null, 2),
    description: item.description,
  })
  dialogVisible.value = true
}

function editStrategy(row: any) {
  isEditing.value = true
  Object.assign(form, { ...row, params: JSON.stringify(row.params || {}, null, 2) })
  dialogVisible.value = true
}

async function saveStrategy() {
  saving.value = true
  try {
    const data = { ...form, params: JSON.parse(form.params || '{}') }
    if (isEditing.value) {
      await strategyApi.updateStrategy(form.id, data)
      ElMessage.success('策略已更新')
    } else {
      await strategyApi.createStrategy(data)
      ElMessage.success('策略已创建')
    }
    dialogVisible.value = false
    await loadStrategies()
  } catch (err) {
    console.error(err)
  } finally {
    saving.value = false
  }
}

async function runStrategy(row: any) {
  runningId.value = row.id
  try {
    await strategyApi.runStrategy(row.id)
    ElMessage.success('策略已触发运行')
  } catch (err: any) {
    const detail = err?.response?.data?.detail || err?.message || '运行失败'
    ElMessage.error(typeof detail === 'string' ? detail : '策略运行失败，请检查策略配置')
  } finally {
    runningId.value = null
  }
}

async function toggleStatus(row: any) {
  try {
    const newStatus = row.status === 'active' ? 'paused' : 'active'
    await strategyApi.updateStrategy(row.id, { status: newStatus })
    ElMessage.success(newStatus === 'active' ? '策略已启用' : '策略已暂停')
    await loadStrategies()
  } catch (err) { console.error('Operation failed:', err); }
}

async function deleteStrategy(id: number) {
  try {
    await strategyApi.deleteStrategy(id)
    ElMessage.success('策略已删除')
    await loadStrategies()
  } catch (err) { console.error('Operation failed:', err); }
}

async function runRegressionTest() {
  testing.value = true
  regressionResult.value = null
  try {
    const res: any = await strategyApi.runRegressionTest()
    regressionResult.value = res.data
    ElMessage.success('回归测试完成')
  } catch (err: any) {
    ElMessage.error(err?.detail || '回归测试失败')
  } finally {
    testing.value = false
  }
}

onMounted(() => {
  loadStrategies()
  loadSymbols()
})
</script>

<style scoped>
.classic-card {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.classic-card .el-card__body {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.classic-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.classic-name {
  font-size: 16px;
  font-weight: 600;
}
.classic-desc {
  color: #606266;
  font-size: 13px;
  line-height: 1.6;
  margin: 8px 0;
  flex: 1;
}
.param-item {
  font-size: 13px;
  margin-bottom: 4px;
  color: #606266;
}
.param-item code {
  background: #f5f7fa;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 12px;
  color: #409eff;
}
.stat-item {
  text-align: center;
  padding: 8px 24px;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #303133;
}
.stat-label {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}
</style>