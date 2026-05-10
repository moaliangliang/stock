<template>
  <div class="decision-page">
    <!-- 统计卡片 -->
    <el-row :gutter="16" class="summary-row">
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">活跃建议</div>
          <div class="stat-value">{{ summary.total_active }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">买入信号</div>
          <div class="stat-value buy">{{ summary.strong_buy_count + summary.buy_count }}</div>
          <div class="stat-sub">强买 {{ summary.strong_buy_count }} / 买入 {{ summary.buy_count }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">平均置信度</div>
          <div class="stat-value">{{ summary.avg_confidence }}%</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">历史准确率</div>
          <div class="stat-value" :class="outcomeSummary.win_rate >= 60 ? 'buy' : outcomeSummary.win_rate >= 40 ? '' : 'sell'">
            {{ outcomeSummary.win_rate }}%
          </div>
          <div class="stat-sub">{{ outcomeSummary.total }}条验证 / 胜{{ outcomeSummary.wins }}场</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Tabs -->
    <el-tabs v-model="activeTab" type="border-card" class="decision-tabs">
      <!-- 当前建议 -->
      <el-tab-pane label="当前建议" name="active">
        <div v-loading="loadingDecisions">
          <div v-if="decisions.length === 0" class="empty-hint">
            <el-empty description="暂无活跃决策建议" />
          </div>
          <el-row :gutter="16" v-else>
            <el-col
              v-for="d in decisions"
              :key="d.id"
              :xs="24"
              :sm="12"
              :md="8"
              :lg="6"
              class="decision-col"
            >
              <el-card shadow="hover" class="decision-card" :class="'card-' + d.recommendation">
                <div class="card-header">
                  <span class="symbol">{{ getSymbolLabel(d.symbol) }}</span>
                  <el-tag :type="recConfig[d.recommendation]?.type || 'info'" size="small">
                    {{ recConfig[d.recommendation]?.label || d.recommendation }}
                  </el-tag>
                </div>

                <div class="regime-tag" v-if="d.factors?.regime">
                  <el-tag size="small" type="info" effect="plain">
                    {{ regimeLabels[d.factors.regime] || d.factors.regime }}
                  </el-tag>
                </div>

                <div class="confidence-section">
                  <span class="conf-label">置信度</span>
                  <el-progress
                    :percentage="d.confidence"
                    :stroke-width="8"
                    :color="confidenceColor(d.confidence)"
                  />
                </div>

                <div class="price-row" v-if="d.target_price || d.stop_loss">
                  <span v-if="d.target_price">目标: ¥{{ d.target_price }}</span>
                  <span v-if="d.stop_loss" class="stop-loss">止损: ¥{{ d.stop_loss }}</span>
                </div>

                <el-collapse class="factor-collapse">
                  <el-collapse-item title="因子分析">
                    <div class="factor-item" v-if="d.factors?.technical">
                      <span class="factor-label">技术面</span>
                      <el-progress
                        :percentage="d.factors.technical.score"
                        :stroke-width="6"
                        :show-text="true"
                        :text-inside="false"
                      />
                    </div>
                    <div class="factor-item" v-if="d.factors?.sentiment">
                      <span class="factor-label">情绪面</span>
                      <el-progress
                        :percentage="d.factors.sentiment.score"
                        :stroke-width="6"
                        :show-text="true"
                        :text-inside="false"
                      />
                    </div>
                    <div class="factor-item" v-if="d.factors?.risk">
                      <span class="factor-label">风险面</span>
                      <el-progress
                        :percentage="d.factors.risk.score"
                        :stroke-width="6"
                        :show-text="true"
                        :text-inside="false"
                      />
                    </div>
                    <div class="factor-item" v-if="d.factors?.momentum">
                      <span class="factor-label">动量</span>
                      <el-progress
                        :percentage="d.factors.momentum.score"
                        :stroke-width="6"
                        :show-text="true"
                        :text-inside="false"
                      />
                    </div>
                    <div class="factor-item" v-if="d.factors?.fundamental">
                      <span class="factor-label">基本面</span>
                      <el-progress
                        :percentage="d.factors.fundamental.score"
                        :stroke-width="6"
                        :show-text="true"
                        :text-inside="false"
                      />
                    </div>
                    <div class="factor-item" v-if="d.factors?.weekly_technical">
                      <span class="factor-label">周线技术面</span>
                      <el-progress
                        :percentage="d.factors.weekly_technical.score"
                        :stroke-width="6"
                        :show-text="true"
                        :text-inside="false"
                      />
                    </div>
                    <div class="factor-signals" v-if="d.factors">
                      <p
                        v-for="(sig, i) in getAllSignals(d.factors).slice(0, 6)"
                        :key="i"
                        class="signal-text"
                      >{{ sig }}</p>
                    </div>
                  </el-collapse-item>
                </el-collapse>

                <div class="card-actions" v-if="d.status === 'active'">
                  <el-button size="small" type="primary" @click="handleExecute(d.id)">
                    执行
                  </el-button>
                  <el-button size="small" @click="handleDismiss(d.id)">
                    忽略
                  </el-button>
                </div>
                <div class="card-status" v-else>
                  <el-tag size="small" type="info">{{ statusLabel(d.status) }}</el-tag>
                </div>
              </el-card>
            </el-col>
          </el-row>
        </div>
      </el-tab-pane>

      <!-- 历史记录 -->
      <el-tab-pane label="历史记录" name="history">
        <div v-loading="loadingHistory">
          <!-- 准确率细分 -->
          <el-card shadow="hover" class="accuracy-breakdown" v-if="outcomeSummary.total > 0">
            <div class="accuracy-title">按信号类型准确率</div>
            <el-row :gutter="12">
              <el-col :span="4" v-for="rec in ['strong_buy','buy','hold','sell','strong_sell']" :key="rec">
                <div class="accuracy-item">
                  <span class="acc-label">{{ recConfig[rec]?.label || rec }}</span>
                  <span class="acc-value" :class="getAccClass(rec)">{{ getAccValue(rec) }}%</span>
                </div>
              </el-col>
            </el-row>
          </el-card>

          <el-table :data="history" stripe v-if="history.length > 0" style="margin-top:12px">
            <el-table-column label="股票" width="180">
              <template #default="{ row }">
                {{ getSymbolLabel(row.symbol) }}
              </template>
            </el-table-column>
            <el-table-column prop="recommendation" label="建议" width="100">
              <template #default="{ row }">
                <el-tag :type="recConfig[row.recommendation]?.type || 'info'" size="small">
                  {{ recConfig[row.recommendation]?.label || row.recommendation }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="confidence" label="置信度" width="90">
              <template #default="{ row }">
                <el-progress :percentage="row.confidence" :stroke-width="6" :show-text="true" />
              </template>
            </el-table-column>
            <el-table-column label="市场状态" width="100">
              <template #default="{ row }">
                <span v-if="row.factors?.regime" class="regime-text">
                  {{ regimeLabels[row.factors.regime] || row.factors.regime }}
                </span>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="statusTagType(row.status)">
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="验证结果" width="100">
              <template #default="{ row }">
                <el-tag
                  v-if="getOutcomeForDecision(row.id)"
                  size="small"
                  :type="outcomeConfig[getOutcomeForDecision(row.id)?.outcome || '']?.type || 'info'"
                >
                  {{ outcomeConfig[getOutcomeForDecision(row.id)?.outcome || '']?.label || '-' }}
                  <span v-if="getOutcomeForDecision(row.id)?.pnl_pct != null">
                    {{ getOutcomeForDecision(row.id)?.pnl_pct! > 0 ? '+' : '' }}{{ getOutcomeForDecision(row.id)?.pnl_pct }}%
                  </span>
                </el-tag>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="生成时间" width="180">
              <template #default="{ row }">
                {{ formatTime(row.created_at) }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-popconfirm title="确认执行此决策?" @confirm="handleExecute(row.id)">
                  <template #reference>
                    <el-button size="small" type="primary" text v-if="row.status === 'active'">
                      执行
                    </el-button>
                  </template>
                </el-popconfirm>
                <el-button size="small" type="info" text @click="showDetail(row)">
                  详情
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="暂无历史记录" />
        </div>
      </el-tab-pane>

      <!-- 批量分析 -->
      <el-tab-pane label="批量分析" name="batch">
        <el-card shadow="hover" class="batch-card">
          <p class="batch-desc">选择或输入需要分析的股票代码（支持6位简码），系统将基于多因子模型生成投资决策建议。</p>
          <div v-if="watchedSymbols.length > 0" style="margin-bottom: 10px">
            <span style="font-size: 12px; color: #e6a23c">⭐ 自选 {{ watchedSymbols.length }} 只：</span>
            <el-tag
              v-for="s in watchedSymbols"
              :key="s"
              size="small"
              type="warning"
              style="margin: 2px 4px; cursor: pointer"
              @click="selectedSymbols.push(s); onSelectChange([])"
            >
              {{ getSymbolLabel(s) || s }}
            </el-tag>
          </div>
          <div class="batch-form">
            <!-- 下拉选择（可多选、可输入新代码） -->
            <el-select
              v-model="selectedSymbols"
              multiple
              filterable
              allow-create
              default-first-option
              :reserve-keyword="false"
              placeholder="选择股票或输入代码（支持6位简码）"
              style="width: 500px"
              popper-class="batch-symbol-select"
              @change="onSelectChange"
            >
              <el-option
                v-for="s in allKnownSymbols"
                :key="s"
                :label="getSymbolLabel(s)"
                :value="s"
              >
                <span style="display: flex; align-items: center; justify-content: space-between; width: 100%">
                  <span>
                    <div>{{ symbolNameMap[s] || s }}</div>
                    <div style="font-size:11px;color:#999">({{ s }})</div>
                  </span>
                  <span
                    style="cursor: pointer; font-size: 14px"
                    @click.stop="toggleWatch(s)"
                  >{{ isWatched(s) ? '⭐' : '☆' }}</span>
                </span>
              </el-option>
            </el-select>
            <el-button type="primary" :loading="generating" @click="handleGenerate" style="margin-left: 12px; align-self: flex-start">
              开始分析
            </el-button>
          </div>
          <div style="margin-top: 8px; font-size: 12px">
            <span style="color: #909399">已选 {{ resolvedSymbols.length }} 个代码：</span>
            <span class="symbol-tag-row" v-for="s in resolvedSymbols" :key="s" style="display: inline-flex; align-items: center; margin: 2px 2px">
              <el-tag
                size="small"
                :type="knownSymbolSet.has(s) ? 'success' : 'info'"
                closable
                @close="removeSymbol(s)"
              >
                {{ getSymbolLabel(s) || s }}
              </el-tag>
              <el-button
                size="small"
                text
                style="padding: 0 2px; margin-left: 0; height: 20px"
                @click="toggleWatch(s)"
                :title="isWatched(s) ? '取消自选' : '加入自选'"
              >
                {{ isWatched(s) ? '⭐' : '☆' }}
              </el-button>
            </span>
            <span v-if="resolvedSymbols.length === 0" style="color: #c0c4cc">—</span>
            <span v-if="watchedSymbols.length > 0" style="color: #e6a23c; margin-left: 8px">⭐ {{ watchedSymbols.length }}只自选</span>
          </div>

          <div v-if="generatedResults.length > 0" class="batch-results" style="margin-top: 20px">
            <el-table :data="generatedResults" stripe>
              <el-table-column label="股票" width="180">
                <template #default="{ row }">
                  {{ getSymbolLabel(row.symbol) }}
                </template>
              </el-table-column>
              <el-table-column prop="recommendation" label="建议" width="110">
                <template #default="{ row }">
                  <el-tag :type="recConfig[row.recommendation]?.type || 'info'" size="small">
                    {{ recConfig[row.recommendation]?.label || row.recommendation }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="confidence" label="置信度" width="100">
                <template #default="{ row }">
                  <el-progress :percentage="row.confidence" :stroke-width="6" />
                </template>
              </el-table-column>
              <el-table-column label="市场状态" width="100">
                <template #default="{ row }">
                  <span v-if="row.factors?.regime">{{ regimeLabels[row.factors.regime] || row.factors.regime }}</span>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column prop="target_price" label="目标价" width="100" />
              <el-table-column prop="stop_loss" label="止损价" width="100" />
              <el-table-column label="因子得分" min-width="180">
                <template #default="{ row }">
                  <span class="factor-scores">
                    T{{ row.factors?.technical_score || '-' }}
                    S{{ row.factors?.sentiment_score || '-' }}
                    R{{ row.factors?.risk_score || '-' }}
                    M{{ row.factors?.momentum_score || '-' }}
                    F{{ row.factors?.fundamental_score || '-' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="reasoning" label="分析概要" min-width="280">
                <template #default="{ row }">
                  <el-popover placement="left" :width="500" trigger="hover" :show-after="300">
                    <template #reference>
                      <span class="reasoning-text">{{ row.reasoning?.replace(/\n/g, ' ').slice(0, 100) }}{{ (row.reasoning || '').length > 100 ? '...' : '' }}</span>
                    </template>
                    <div style="position: relative">
                      <el-button size="small" type="primary" text style="position: absolute; top: 0; right: 0" @click="copyReasoning(row.reasoning)">
                        复制
                      </el-button>
                      <pre class="reasoning-popover" style="margin-top: 0">{{ row.reasoning }}</pre>
                    </div>
                  </el-popover>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 详情对话框 -->
    <el-dialog v-model="detailVisible" title="决策详情" width="700px">
      <template v-if="detailData">
        <div class="detail-header">
          <span class="detail-symbol">{{ getSymbolLabel(detailData.symbol) }}</span>
          <el-tag :type="recConfig[detailData.recommendation]?.type || 'info'">
            {{ recConfig[detailData.recommendation]?.label }}
          </el-tag>
          <span class="detail-confidence">置信度: {{ detailData.confidence }}%</span>
          <span v-if="detailData.factors?.regime" class="detail-regime">
            市场: {{ regimeLabels[detailData.factors.regime] || detailData.factors.regime }}
          </span>
        </div>
        <div v-if="detailData.target_price || detailData.stop_loss" class="detail-prices">
          <span v-if="detailData.target_price">目标价: ¥{{ detailData.target_price }}</span>
          <span v-if="detailData.stop_loss" style="margin-left:16px">止损价: ¥{{ detailData.stop_loss }}</span>
        </div>
        <div class="detail-reasoning" v-if="detailData.reasoning">
          <pre>{{ detailData.reasoning }}</pre>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { decisionApi } from '@/api/decision'
import { marketApi } from '@/api/market'
import { recommendationConfig, outcomeConfig, regimeLabels } from '@/types/decision'
import type { InvestmentDecision, DecisionSummary, OutcomeSummary } from '@/types/decision'

const activeTab = ref('active')
const loadingDecisions = ref(false)
const loadingHistory = ref(false)
const generating = ref(false)
const detailVisible = ref(false)

const summary = reactive<DecisionSummary>({
  total_active: 0,
  strong_buy_count: 0,
  buy_count: 0,
  hold_count: 0,
  sell_count: 0,
  strong_sell_count: 0,
  avg_confidence: 0,
  top_picks: [],
  recent_decisions: [],
})

const outcomeSummary = reactive<OutcomeSummary>({
  total: 0,
  wins: 0,
  losses: 0,
  breakeven_count: 0,
  win_rate: 0,
  avg_pnl_pct: 0,
  strong_buy_accuracy: 0,
  buy_accuracy: 0,
  hold_accuracy: 0,
  sell_accuracy: 0,
  strong_sell_accuracy: 0,
  recent_outcomes: [],
})

const decisions = ref<InvestmentDecision[]>([])
const history = ref<InvestmentDecision[]>([])
const generatedResults = ref<InvestmentDecision[]>([])
const detailData = ref<InvestmentDecision | null>(null)
const selectedSymbols = ref<string[]>([])
const symbolNameMap = ref<Record<string, string>>({})
const watchedSymbols = ref<string[]>([])

function isWatched(sym: string): boolean {
  return watchedSymbols.value.includes(sym)
}

async function toggleWatch(symbol: string) {
  try {
    const res = await marketApi.toggleWatched(symbol)
    if (res.code === 200) {
      if (res.data?.is_watched) {
        if (!watchedSymbols.value.includes(symbol)) {
          watchedSymbols.value.push(symbol)
        }
      } else {
        watchedSymbols.value = watchedSymbols.value.filter(s => s !== symbol)
      }
      marketApi.clearSymbolsCache()
    }
  } catch { /* ignore */ }
}

// All known symbols from DB + localStorage (for dropdown options)
const allKnownSymbols = computed<string[]>(() => {
  const dbSymbols = Object.keys(symbolNameMap.value).filter(s => s.endsWith('.SH') || s.endsWith('.SZ'))
  const stored: string[] = []
  try {
    const raw = localStorage.getItem('batch_symbols') || '[]'
    stored.push(...JSON.parse(raw))
  } catch { /* ignore */ }
  // Merge, dedup, sort
  return [...new Set([...dbSymbols, ...stored])].sort()
})

const knownSymbolSet = computed(() => new Set(allKnownSymbols.value))

// Resolve a raw code to full symbol: 6 digits → .SH/.SZ, full format → uppercase
function resolveCode(raw: string): string | null {
  const trimmed = raw.trim().toUpperCase()
  // Already full format
  if (/^\d{6}\.(SH|SZ)$/i.test(trimmed)) return trimmed
  // 6-digit code: try DB lookup first, then heuristic
  if (/^\d{6}$/.test(trimmed)) {
    // Look up in symbolNameMap
    const match = Object.keys(symbolNameMap.value).find(s => s.startsWith(trimmed))
    if (match) return match
    // Heuristic: A-share code ranges
    // 600xxx/601xxx/603xxx/605xxx → SH; 510xxx/512xxx/588xxx → SH
    if (/^[56]/.test(trimmed)) return trimmed + '.SH'
    // 000xxx/001xxx/002xxx/003xxx/300xxx/159xxx → SZ
    if (/^[0123]/.test(trimmed)) return trimmed + '.SZ'
    return null
  }
  return null
}

// All currently validated symbols from the select (deduped)
const resolvedSymbols = computed<string[]>(() => {
  return [...new Set(selectedSymbols.value.map(s => resolveCode(s)).filter(Boolean))] as string[]
})

// When user selects from dropdown or types a new tag, save to localStorage
function onSelectChange(_vals: string[]) {
  // Drop invalid entries (e.g. random text that can't resolve to a symbol)
  selectedSymbols.value = selectedSymbols.value.filter(s => resolveCode(s) !== null)
  persistNewSymbols()
}

function removeSymbol(sym: string) {
  // Remove from selectedSymbols by finding the pre-resolved entry
  selectedSymbols.value = selectedSymbols.value.filter(s => resolveCode(s) !== sym)
  // Clean up invalid entries
  selectedSymbols.value = selectedSymbols.value.filter(s => resolveCode(s) !== null)
}

function persistNewSymbols() {
  try {
    const existing: string[] = JSON.parse(localStorage.getItem('batch_symbols') || '[]')
    const resolved = resolvedSymbols.value
    const merged = [...new Set([...existing, ...resolved])]
    localStorage.setItem('batch_symbols', JSON.stringify(merged))
  } catch { /* ignore */ }
}

// Outcome lookup: decision_id -> outcome
const outcomeMap = ref<Record<number, any>>({})

function getSymbolLabel(symbol: string): string {
  const name = symbolNameMap.value[symbol]
  return name ? `${name} (${symbol})` : symbol
}

const recConfig = recommendationConfig

function confidenceColor(val: number): string {
  if (val >= 80) return '#52c41a'
  if (val >= 60) return '#95de64'
  if (val >= 40) return '#909399'
  if (val >= 20) return '#faad14'
  return '#f5222d'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    active: '活跃',
    executed: '已执行',
    dismissed: '已忽略',
    expired: '已过期',
  }
  return map[status] || status
}

function statusTagType(status: string): string {
  const map: Record<string, string> = {
    active: 'success',
    executed: 'primary',
    dismissed: 'info',
    expired: 'warning',
  }
  return map[status] || ''
}

function getOutcomeForDecision(decisionId: number): any | null {
  return outcomeMap.value[decisionId] || null
}

function getAccValue(rec: string): number {
  const key = `${rec}_accuracy` as keyof OutcomeSummary
  return (outcomeSummary[key] as number) || 0
}

function getAccClass(rec: string): string {
  const val = getAccValue(rec)
  if (val >= 60) return 'buy'
  if (val >= 40) return ''
  return 'sell'
}

function formatTime(ts: string): string {
  if (!ts) return '-'
  return new Date(ts).toLocaleString('zh-CN')
}

async function copyReasoning(text: string) {
  try {
    await navigator.clipboard.writeText(text || '')
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败')
  }
}

function getAllSignals(factors: any): string[] {
  const keys = ['technical', 'sentiment', 'risk', 'momentum', 'fundamental']
  const all: string[] = []
  for (const k of keys) {
    const sigs = factors?.[k]?.details?.signals
    if (Array.isArray(sigs)) all.push(...sigs)
  }
  return all
}

function showDetail(row: InvestmentDecision) {
  detailData.value = row
  detailVisible.value = true
}

async function loadSummary() {
  try {
    const res = await decisionApi.getSummary()
    if (res.code === 200) {
      Object.assign(summary, res.data)
    }
  } catch {
    // ignore
  }
}

async function loadOutcomeSummary() {
  try {
    const res = await decisionApi.getOutcomeSummary(30)
    if (res.code === 200) {
      Object.assign(outcomeSummary, res.data)
      // Build outcome lookup map
      const outcomes = res.data?.recent_outcomes || []
      for (const o of outcomes) {
        outcomeMap.value[o.decision_id] = o
      }
    }
  } catch {
    // ignore
  }
}

async function loadDecisions() {
  loadingDecisions.value = true
  try {
    const res = await decisionApi.getDecisions({ status: 'active', page_size: 50 })
    if (res.code === 200) {
      decisions.value = res.data?.items || []
    }
  } finally {
    loadingDecisions.value = false
  }
}

async function loadHistory() {
  loadingHistory.value = true
  try {
    const res = await decisionApi.getDecisions({ page_size: 100 })
    if (res.code === 200) {
      history.value = res.data?.items || []
    }
  } finally {
    loadingHistory.value = false
  }
}

async function handleGenerate() {
  // Save manually entered codes before generating
  persistNewSymbols()
  if (resolvedSymbols.value.length === 0) {
    ElMessage.warning('请选择或输入至少一个股票代码（格式如 000001.SZ，支持6位简码）')
    return
  }
  generating.value = true
  try {
    const res = await decisionApi.generate(resolvedSymbols.value)
    if (res.code === 200) {
      generatedResults.value = res.data || []
      ElMessage.success(`生成完成，共 ${generatedResults.value.length} 条建议`)
      loadSummary()
      loadDecisions()
    }
  } catch {
    ElMessage.error('生成失败')
  } finally {
    generating.value = false
  }
}

async function handleExecute(id: number) {
  try {
    const res = await decisionApi.execute(id)
    if (res.code === 200) {
      ElMessage.success('已执行')
      loadDecisions()
      loadHistory()
    }
  } catch {
    ElMessage.error('操作失败')
  }
}

async function handleDismiss(id: number) {
  try {
    const res = await decisionApi.dismiss(id)
    if (res.code === 200) {
      ElMessage.success('已忽略')
      loadDecisions()
    }
  } catch {
    ElMessage.error('操作失败')
  }
}

async function loadSymbolNames() {
  try {
    const res = await marketApi.getSymbols()
    if (res.code === 200) {
      const list = res.data || []
      for (const s of list) {
        symbolNameMap.value[s.symbol] = s.name
        if (s.is_watched && !watchedSymbols.value.includes(s.symbol)) {
          watchedSymbols.value.push(s.symbol)
        }
      }
    }
  } catch {
    // ignore
  }
}

onMounted(() => {
  loadSymbolNames()
  loadSummary()
  loadOutcomeSummary()
  loadDecisions()
  loadHistory()
})
</script>

<style scoped>
.decision-page { padding: 4px 0; }
.summary-row { margin-bottom: 16px; }
.stat-card { text-align: center; }
.stat-label { color: #909399; font-size: 14px; margin-bottom: 8px; }
.stat-value { font-size: 28px; font-weight: bold; color: #303133; }
.stat-value.buy { color: #52c41a; }
.stat-value.sell { color: #f5222d; }
.stat-sub { font-size: 12px; color: #909399; margin-top: 4px; }

.decision-tabs { margin-top: 16px; }

.decision-col { margin-bottom: 16px; }
.decision-card { border-top: 3px solid #ddd; }
.decision-card.card-strong_buy { border-top-color: #52c41a; }
.decision-card.card-buy { border-top-color: #95de64; }
.decision-card.card-hold { border-top-color: #909399; }
.decision-card.card-sell { border-top-color: #faad14; }
.decision-card.card-strong_sell { border-top-color: #f5222d; }

.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.symbol { font-size: 16px; font-weight: 600; }

.regime-tag { margin-bottom: 8px; }

.confidence-section { margin-bottom: 12px; }
.conf-label { font-size: 12px; color: #909399; display: block; margin-bottom: 4px; }

.price-row { display: flex; justify-content: space-between; font-size: 13px; color: #606266; margin-bottom: 8px; }
.stop-loss { color: #f5222d; }

.factor-collapse { margin-top: 4px; }
.factor-item { margin-bottom: 8px; }
.factor-label { font-size: 12px; color: #606266; display: block; margin-bottom: 2px; }
.factor-signals { margin-top: 8px; }
.signal-text { font-size: 12px; color: #909399; margin: 2px 0; padding-left: 8px; border-left: 2px solid #ddd; }

.card-actions { display: flex; gap: 8px; margin-top: 12px; }
.card-status { margin-top: 12px; text-align: center; }

.reasoning-text { font-size: 12px; color: #606266; cursor: pointer; }
.reasoning-popover { white-space: pre-wrap; font-size: 13px; line-height: 1.7; color: #303133; max-height: 400px; overflow-y: auto; margin: 0; }

.detail-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.detail-symbol { font-size: 18px; font-weight: 600; }
.detail-confidence { color: #909399; }
.detail-regime { color: #909399; font-size: 13px; }
.detail-prices { font-size: 14px; color: #606266; margin-bottom: 16px; }
.detail-reasoning pre { white-space: pre-wrap; font-family: inherit; font-size: 14px; line-height: 1.8; color: #303133; background: #f5f7fa; padding: 16px; border-radius: 4px; }

.batch-card { padding: 4px; }
.batch-desc { color: #909399; font-size: 14px; margin-bottom: 16px; }
.batch-form { display: flex; align-items: center; }
.empty-hint { padding: 40px 0; }

.accuracy-breakdown { margin-bottom: 12px; }
.accuracy-title { font-size: 14px; font-weight: 600; color: #303133; margin-bottom: 12px; }
.accuracy-item { text-align: center; }
.acc-label { display: block; font-size: 12px; color: #909399; margin-bottom: 4px; }
.acc-value { font-size: 18px; font-weight: bold; }
.acc-value.buy { color: #52c41a; }
.acc-value.sell { color: #f5222d; }

.regime-text { font-size: 12px; color: #606266; }
.factor-scores { font-size: 12px; color: #909399; letter-spacing: 2px; }
</style>
