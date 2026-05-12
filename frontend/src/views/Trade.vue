<template>
  <div>
    <h3 style="margin-bottom: 20px">💰 实盘交易</h3>

    <el-row :gutter="16">
      <el-col :span="8">
        <el-card shadow="hover">
          <template #header><span>📝 委托下单</span></template>
          <el-form :model="orderForm" label-width="80px" size="small">
            <el-form-item label="标的">
              <el-select v-model="orderForm.symbol" filterable style="width: 100%">
                <el-option v-for="s in symbols" :key="s.symbol" :label="`${s.name} ${s.symbol}`" :value="s.symbol">
                  <div>{{ s.name }}</div>
                  <div style="font-size:11px;color:#999">({{ s.symbol }})</div>
                </el-option>
              </el-select>
            </el-form-item>
            <el-form-item label="方向">
              <el-radio-group v-model="orderForm.side">
                <el-radio-button value="buy">买入</el-radio-button>
                <el-radio-button value="sell">卖出</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="类型">
              <el-select v-model="orderForm.type" style="width: 100%">
                <el-option label="限价单" value="limit" />
                <el-option label="市价单" value="market" />
              </el-select>
            </el-form-item>
            <el-form-item label="价格" v-if="orderForm.type === 'limit'">
              <el-input-number v-model="orderForm.price" :min="0.01" :step="0.01" style="width: 100%" />
            </el-form-item>
            <el-form-item label="数量">
              <el-input-number v-model="orderForm.quantity" :min="1" :step="100" style="width: 100%" />
            </el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                :loading="submitting"
                style="width: 100%"
                @click="submitOrder"
              >
                {{ submitting ? '提交中...' : '提交订单' }}
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <el-col :span="16">
        <!-- 汇总卡片 -->
        <el-row :gutter="12" style="margin-bottom: 12px">
          <el-col :span="6">
            <div class="summary-card">
              <div class="summary-label">总市值</div>
              <div class="summary-value">{{ totalMarketValue.toLocaleString() }}</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="summary-card">
              <div class="summary-label">总成本</div>
              <div class="summary-value">{{ totalCost.toLocaleString() }}</div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="summary-card">
              <div class="summary-label">浮动盈亏</div>
              <div class="summary-value" :class="totalPnl >= 0 ? 'price-up' : 'price-down'">
                {{ totalPnl.toLocaleString() }}
                <span style="font-size:12px;margin-left:2px">{{ totalPnlRatio >= 0 ? '+' : '' }}{{ totalPnlRatio }}%</span>
              </div>
            </div>
          </el-col>
          <el-col :span="6">
            <div class="summary-card">
              <div class="summary-label">当日盈亏</div>
              <div class="summary-value" :class="totalDayPnl >= 0 ? 'price-up' : 'price-down'">
                {{ totalDayPnl.toLocaleString() }}
                <span style="font-size:12px;margin-left:2px">{{ totalDayPnlRatio >= 0 ? '+' : '' }}{{ totalDayPnlRatio }}%</span>
              </div>
            </div>
          </el-col>
        </el-row>

        <el-card shadow="hover" style="margin-bottom: 16px">
          <template #header>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span>📦 当前持仓</span>
              <div>
                <span v-if="lastSyncTime" style="font-size:12px;color:#999;margin-right:8px">
                  上次同步: {{ lastSyncTime }}
                </span>
                <el-button size="small" type="success" :loading="syncLoading" @click="syncPositions">同步持仓</el-button>
                <el-button size="small" type="primary" @click="showPosDialog = true">手动录入</el-button>
                <el-button size="small" @click="showImportDialog = true">Excel 导入</el-button>
              </div>
            </div>
          </template>
          <el-table :data="positions" stripe size="small" v-loading="posLoading" :default-sort="{prop: 'market_value', order: 'descending'}">
            <el-table-column prop="symbol" label="证券名称" width="125" sortable>
              <template #default="{ row }">
                <div style="font-weight:500">{{ getSymbolName(row.symbol) }}</div>
                <div style="font-size:11px;color:#999">{{ row.symbol }}</div>
              </template>
            </el-table-column>
            <el-table-column prop="quantity" label="持仓/可用" width="95" sortable>
              <template #default="{ row }">
                <div>{{ row.quantity }}</div>
                <div style="font-size:11px;color:#999">{{ row.available_quantity ?? row.quantity }}</div>
              </template>
            </el-table-column>
            <el-table-column prop="cost_price" label="成本" width="80" sortable>
              <template #default="{ row }">
                {{ row.cost_price < 0 ? '已回本' : (row.cost_price || 0).toFixed(3) }}
              </template>
            </el-table-column>
            <el-table-column prop="current_price" label="现价" width="80" sortable>
              <template #default="{ row }">
                <span :class="(row.day_pnl_ratio || 0) >= 0 ? 'price-up' : 'price-down'">
                  {{ (row.current_price || 0).toFixed(3) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="market_value" label="市值" width="110" sortable>
              <template #default="{ row }">
                <div>{{ (row.market_value || 0).toLocaleString() }}</div>
                <div style="font-size:11px;color:#999">{{ positionRatio(row) }}%</div>
              </template>
            </el-table-column>
            <el-table-column prop="pnl" label="浮动盈亏" width="130" sortable>
              <template #default="{ row }">
                <div :class="(row.pnl || 0) >= 0 ? 'price-up' : 'price-down'">
                  {{ (row.pnl || 0) >= 0 ? '+' : '' }}{{ (row.pnl || 0).toLocaleString() }}
                </div>
                <div v-if="row.cost_price < 0" style="font-size:11px;color:#e6a23c">已回本</div>
                <div v-else :class="(row.pnl_ratio || 0) >= 0 ? 'price-up' : 'price-down'" style="font-size:11px">
                  {{ (row.pnl_ratio || 0) >= 0 ? '+' : '' }}{{ (row.pnl_ratio || 0).toFixed(2) }}%
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="day_pnl" label="当日盈亏" width="120" sortable>
              <template #default="{ row }">
                <div :class="(row.day_pnl || 0) >= 0 ? 'price-up' : 'price-down'">
                  {{ (row.day_pnl || 0) >= 0 ? '+' : '' }}{{ (row.day_pnl || 0).toLocaleString() }}
                </div>
                <div :class="(row.day_pnl_ratio || 0) >= 0 ? 'price-up' : 'price-down'" style="font-size:11px">
                  {{ (row.day_pnl_ratio || 0) >= 0 ? '+' : '' }}{{ (row.day_pnl_ratio || 0).toFixed(2) }}%
                </div>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="hover">
          <template #header><span>📋 委托记录</span></template>
          <el-table :data="orders" stripe size="small" v-loading="orderLoading">
            <el-table-column prop="symbol" label="标的" width="90" />
            <el-table-column prop="side" label="方向" width="60">
              <template #default="{ row }">
                <el-tag :type="row.side === 'buy' ? 'danger' : 'success'" size="small">
                  {{ row.side === 'buy' ? '买' : '卖' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="type" label="类型" width="60" />
            <el-table-column prop="price" label="价格" width="80" />
            <el-table-column prop="quantity" label="数量" width="70" />
            <el-table-column prop="filled_quantity" label="已成交" width="70" />
            <el-table-column prop="status" label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="orderStatusType(row.status)" size="small">
                  {{ orderStatusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80" fixed="right">
              <template #default="{ row }">
                <el-button
                  v-if="row.status === 'pending' || row.status === 'partial'"
                  size="small"
                  type="danger"
                  @click="cancelOrder(row.id)"
                >撤单</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <!-- 手动录入持仓对话框 -->
    <el-dialog v-model="showPosDialog" title="手动录入持仓" width="420px" @closed="resetPosForm">
      <el-form :model="posForm" label-width="80px" size="small">
        <el-form-item label="标的代码">
          <el-select v-model="posForm.symbol" filterable allow-create style="width:100%" placeholder="如 600519.SH">
            <el-option v-for="s in symbols" :key="s.symbol" :label="`${s.name} ${s.symbol}`" :value="s.symbol">
              <div>{{ s.name }}</div>
              <div style="font-size:11px;color:#999">({{ s.symbol }})</div>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="持仓数量">
          <el-input-number v-model="posForm.quantity" :min="1" :step="100" style="width:100%" />
        </el-form-item>
        <el-form-item label="成本价">
          <el-input-number v-model="posForm.cost_price" :step="0.01" :precision="2" style="width:100%" />
        </el-form-item>
        <el-form-item label="杠杆">
          <el-input-number v-model="posForm.leverage" :min="1" :step="1" style="width:100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPosDialog = false">取消</el-button>
        <el-button type="primary" :loading="posSubmitting" @click="submitPosition">确认录入</el-button>
      </template>
    </el-dialog>

    <!-- Excel 导入持仓对话框 -->
    <el-dialog v-model="showImportDialog" title="Excel 导入持仓" width="520px" @closed="resetImport">
      <div style="margin-bottom:12px;font-size:13px;color:#666">
        支持 .xlsx / .xls 格式。表头需包含：<b>代码</b>（symbol）、<b>数量</b>（quantity）、<b>成本价</b>（cost_price），<b>杠杆</b>（leverage）可选。
      </div>
      <el-upload
        ref="uploadRef"
        drag
        :auto-upload="false"
        :limit="1"
        accept=".xlsx,.xls"
        :on-change="handleFileChange"
        :on-remove="() => importFile = null"
      >
        <div class="el-upload__text">拖拽文件到此处 或 <em>点击选择</em></div>
      </el-upload>
      <div v-if="importResult" style="margin-top:16px">
        <el-alert
          :type="importResult.errors > 0 ? 'warning' : 'success'"
          :title="`共 ${importResult.total} 条: 新建 ${importResult.created}, 更新 ${importResult.updated}, 失败 ${importResult.errors}`"
          :closable="false"
        />
        <div v-if="importResult.details?.length" style="margin-top:8px;max-height:180px;overflow-y:auto">
          <div v-for="d in importResult.details" :key="d.row" style="font-size:13px;padding:2px 0">
            <el-tag size="small" :type="d.status === 'error' ? 'danger' : d.status === 'created' ? 'success' : 'info'" style="margin-right:6px">
              {{ d.status === 'created' ? '新建' : d.status === 'updated' ? '更新' : '失败' }}
            </el-tag>
            <span>第{{ d.row }}行 {{ d.symbol }}: {{ d.message }}</span>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="showImportDialog = false">关闭</el-button>
        <el-button type="primary" :loading="importSubmitting" :disabled="!importFile" @click="submitImport">上传导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { tradeApi, marketApi } from '@/api'

const submitting = ref(false)
const posLoading = ref(false)
const orderLoading = ref(false)
const syncLoading = ref(false)
const lastSyncTime = ref('')
const symbols = ref<any[]>([])
const positions = ref<any[]>([])
const orders = ref<any[]>([])

// ── 汇总计算 ────────────────────────────────────────
const totalMarketValue = computed(() => {
  return positions.value.reduce((s, p) => s + (Number(p.market_value) || 0), 0)
})
const totalCost = computed(() => {
  return positions.value.reduce((s, p) => s + (Number(p.cost_price) || 0) * (Number(p.quantity) || 0), 0)
})
const totalPnl = computed(() => {
  return positions.value.reduce((s, p) => s + (Number(p.pnl) || 0), 0)
})
const totalPnlRatio = computed(() => {
  const cost = totalCost.value
  if (cost <= 0) return '0.00'
  return ((totalPnl.value / cost) * 100).toFixed(2)
})
const totalDayPnl = computed(() => {
  return positions.value.reduce((s, p) => s + (Number(p.day_pnl) || 0), 0)
})
const totalDayPnlRatio = computed(() => {
  const mv = totalMarketValue.value
  if (mv <= 0) return '0.00'
  // day_pnl_ratio is already per-position; aggregate ratio = sum(day_pnl) / sum(market_value)
  // but market_value already reflects current price. Use prev_day_mv ≈ mv - day_pnl
  const prevMv = mv - totalDayPnl.value
  if (prevMv <= 0) return '0.00'
  return ((totalDayPnl.value / prevMv) * 100).toFixed(2)
})

function positionRatio(row: any): string {
  const mv = totalMarketValue.value
  if (mv <= 0) return '0.00'
  return ((Number(row.market_value) || 0) / mv * 100).toFixed(1)
}

const orderForm = reactive({
  symbol: 'BTC/USDT',
  side: 'buy',
  type: 'limit',
  price: 50000,
  quantity: 100,
})

// ----- 手动录入持仓 -----
const showPosDialog = ref(false)
const posSubmitting = ref(false)
const posForm = reactive({
  symbol: '',
  quantity: 100,
  cost_price: 10,
  leverage: 1,
})

function resetPosForm() {
  posForm.symbol = ''
  posForm.quantity = 100
  posForm.cost_price = 10
  posForm.leverage = 1
}

async function submitPosition() {
  if (!posForm.symbol) {
    ElMessage.warning('请输入标的代码')
    return
  }
  if (posForm.quantity <= 0 || posForm.cost_price === 0) {
    ElMessage.warning('数量必须大于0，成本价不能为0')
    return
  }
  posSubmitting.value = true
  try {
    await tradeApi.createPosition({ ...posForm })
    ElMessage.success('持仓录入成功')
    showPosDialog.value = false
    await loadPositions()
  } catch (err) { console.error('Operation failed:', err); 
    // error handled by interceptor
  } finally { posSubmitting.value = false }
}

// ----- Excel 导入 -----
const showImportDialog = ref(false)
const importSubmitting = ref(false)
const importFile = ref<File | null>(null)
const importResult = ref<any>(null)

function handleFileChange(file: any) {
  importFile.value = file.raw
  importResult.value = null
}

function resetImport() {
  importFile.value = null
  importResult.value = null
}

async function syncPositions() {
  syncLoading.value = true
  try {
    const res: any = await tradeApi.syncPositions()
    const data = res.data || {}
    if (data.total === 0) {
      ElMessage.info('东方财富账号无持仓数据')
    } else {
      ElMessage.success(`同步完成: 新建 ${data.created} 条, 更新 ${data.updated} 条`)
    }
    lastSyncTime.value = new Date().toLocaleString('zh-CN')
    await loadPositions()
  } catch (err: any) {
    const msg = err?.response?.data?.detail || '同步失败，请检查东方财富 token 是否有效'
    ElMessage.error(msg)
  } finally { syncLoading.value = false }
}

async function submitImport() {
  if (!importFile.value) {
    ElMessage.warning('请选择文件')
    return
  }
  importSubmitting.value = true
  try {
    const res: any = await tradeApi.importPositions(importFile.value)
    importResult.value = res.data || {}
    if (importResult.value?.errors === 0) {
      ElMessage.success(`导入完成: 新建 ${importResult.value.created}, 更新 ${importResult.value.updated}`)
    } else {
      ElMessage.warning(`导入完成但有 ${importResult.value.errors} 条失败`)
    }
    await loadPositions()
  } catch (err) { console.error('Operation failed:', err); 
    // error handled by interceptor
  } finally { importSubmitting.value = false }
}

// ----- 现有逻辑 -----
const POSITION_NAMES: Record<string, string> = {
  '002463': '沪电股份', '002475': '立讯精密', '600028': '中国石化',
  '600036': '招商银行', '600789': '鲁抗医药', '601633': '长城汽车',
  '159205': '创业板ETF', '159206': '卫星ETF', '159326': '电网设备',
  '159516': '5G通信', '159599': '芯片指数', '159637': '新能龙头',
  '159941': '纳指ETF', '510050': '上证50', '510330': '沪深300',
  '513180': '恒生科技', '513500': '标普500', '520530': '港科ETF',
  '560860': '有色ETF',
}

function getSymbolName(symbol: string): string {
  const s = symbols.value.find((x: any) => x.symbol === symbol || x.symbol.startsWith(symbol + '.'))
  if (s?.name) return s.name
  return POSITION_NAMES[symbol] || symbol
}

const orderStatusLabel = (s: string) => ({ pending: '待成交', partial: '部分成交', filled: '已成交', canceled: '已撤销', rejected: '已拒绝' }[s] || s)
const orderStatusType = (s: string) => ({ pending: 'warning', partial: 'primary', filled: 'success', canceled: 'info', rejected: 'danger' }[s] || 'info')

async function loadPositions() {
  posLoading.value = true
  try {
    const res: any = await tradeApi.getPositions()
    positions.value = res.data || []
  } finally { posLoading.value = false }
}

async function loadOrders() {
  orderLoading.value = true
  try {
    const res: any = await tradeApi.getOrders()
    orders.value = res.data || []
  } finally { orderLoading.value = false }
}

async function submitOrder() {
  if (!orderForm.symbol) { ElMessage.warning('请选择交易标的'); return }
  if (!orderForm.side) { ElMessage.warning('请选择买卖方向'); return }
  if (orderForm.type === 'limit' && (!orderForm.price || orderForm.price <= 0)) {
    ElMessage.warning('请输入有效价格'); return
  }
  if (!orderForm.quantity || orderForm.quantity <= 0) { ElMessage.warning('请输入有效数量'); return }
  submitting.value = true
  try {
    await tradeApi.createOrder(orderForm as any)
    ElMessage.success('订单提交成功')
    await Promise.all([loadPositions(), loadOrders()])
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '提交失败')
  } finally { submitting.value = false }
}

async function cancelOrder(id: number) {
  try {
    await tradeApi.cancelOrder(id)
    ElMessage.success('撤单成功')
    await loadOrders()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '撤单失败')
  }
}

onMounted(async () => {
  try {
    const res: any = await marketApi.getSymbols()
    symbols.value = res.data || []
  } catch (err) { console.error('Operation failed:', err); }
  loadPositions()
  loadOrders()
})
</script>

<style scoped>
.summary-card {
  background: #f5f7fa;
  border-radius: 8px;
  padding: 12px 14px;
  text-align: center;
}
.summary-card .summary-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
.summary-card .summary-value {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
  font-variant-numeric: tabular-nums;
}
</style>
