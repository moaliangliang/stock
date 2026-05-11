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
        <el-card shadow="hover" style="margin-bottom: 16px">
          <template #header>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span>📦 当前持仓</span>
              <div>
                <el-button size="small" type="primary" @click="showPosDialog = true">手动录入</el-button>
                <el-button size="small" @click="showImportDialog = true">Excel 导入</el-button>
              </div>
            </div>
          </template>
          <el-table :data="positions" stripe size="small" v-loading="posLoading" show-summary :summary-method="positionSummary" :default-sort="{prop: 'market_value', order: 'descending'}">
            <el-table-column prop="symbol" label="标的" width="130" sortable>
              <template #default="{ row }">
                <div>{{ getSymbolName(row.symbol) }}</div>
                <div style="font-size:11px;color:#999">({{ row.symbol }})</div>
              </template>
            </el-table-column>
            <el-table-column prop="quantity" label="持仓" width="80" sortable />
            <el-table-column prop="available_quantity" label="可用" width="80" sortable />
            <el-table-column prop="cost_price" label="成本价" width="90" sortable />
            <el-table-column prop="current_price" label="现价" width="90" sortable />
            <el-table-column prop="market_value" label="市值" width="100" sortable>
              <template #default="{ row }">
                {{ (row.market_value || 0).toLocaleString() }}
              </template>
            </el-table-column>
            <el-table-column prop="pnl" label="盈亏" width="100" sortable>
              <template #default="{ row }">
                <span :class="(row.pnl || 0) >= 0 ? 'price-up' : 'price-down'">
                  {{ (row.pnl || 0).toLocaleString() }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="pnl_ratio" label="盈亏比" width="80" sortable>
              <template #default="{ row }">
                <span :class="(row.pnl_ratio || 0) >= 0 ? 'price-up' : 'price-down'">
                  {{ row.pnl_ratio?.toFixed(2) }}%
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="day_pnl" label="日盈亏" width="100" sortable>
              <template #default="{ row }">
                <span :class="(row.day_pnl || 0) >= 0 ? 'price-up' : 'price-down'">
                  {{ (row.day_pnl || 0).toLocaleString() }}
                </span>
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
          <el-input-number v-model="posForm.cost_price" :min="0.01" :step="0.01" :precision="2" style="width:100%" />
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
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { tradeApi, marketApi } from '@/api'

const submitting = ref(false)
const posLoading = ref(false)
const orderLoading = ref(false)
const symbols = ref<any[]>([])
const positions = ref<any[]>([])
const orders = ref<any[]>([])

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
  if (posForm.quantity <= 0 || posForm.cost_price <= 0) {
    ElMessage.warning('数量和成本价必须大于0')
    return
  }
  posSubmitting.value = true
  try {
    await tradeApi.createPosition({ ...posForm })
    ElMessage.success('持仓录入成功')
    showPosDialog.value = false
    await loadPositions()
  } catch {
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
  } catch {
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

function positionSummary({ columns, data }: any) {
  const sums: string[] = []
  columns.forEach((_: any, idx: number) => {
    if (idx === 0) { sums[idx] = '合计'; return }
    const prop = columns[idx].property
    if (prop === 'market_value' || prop === 'pnl' || prop === 'day_pnl') {
      const total = data.reduce((acc: number, row: any) => acc + (Number(row[prop]) || 0), 0)
      sums[idx] = total.toLocaleString()
    } else {
      sums[idx] = ''
    }
  })
  return sums
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
  submitting.value = true
  try {
    await tradeApi.createOrder(orderForm as any)
    ElMessage.success('订单提交成功')
    await Promise.all([loadPositions(), loadOrders()])
  } catch {
    // error handled by interceptor
  } finally { submitting.value = false }
}

async function cancelOrder(id: number) {
  try {
    await tradeApi.cancelOrder(id)
    ElMessage.success('撤单成功')
    await loadOrders()
  } catch {}
}

onMounted(async () => {
  try {
    const res: any = await marketApi.getSymbols()
    symbols.value = res.data || []
  } catch {}
  loadPositions()
  loadOrders()
})
</script>
