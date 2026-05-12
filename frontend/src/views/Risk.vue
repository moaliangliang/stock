<template>
  <div>
    <h3 style="margin-bottom: 20px">⚠️ 风控管理</h3>

    <el-row :gutter="16">
      <el-col :span="14">
        <el-card shadow="hover">
          <template #header>
            <span>📋 风控规则</span>
            <el-button size="small" type="primary" style="float: right" @click="showAddDialog">
              + 新增规则
            </el-button>
          </template>
          <el-table :data="rules" stripe v-loading="loading" size="small">
            <el-table-column prop="name" label="规则名称" min-width="140" />
            <el-table-column prop="rule_type" label="类型" width="120">
              <template #default="{ row }">
                <el-tag size="small">{{ ruleTypeLabel(row.rule_type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="action" label="动作" width="80">
              <template #default="{ row }">
                <el-tag :type="row.action === 'block' ? 'danger' : 'warning'" size="small">
                  {{ row.action === 'block' ? '阻止' : '警告' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="is_active" label="状态" width="70">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                  {{ row.is_active ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150">
              <template #default="{ row }">
                <el-button size="small" @click="editRule(row)">编辑</el-button>
                <el-popconfirm title="确认删除?" @confirm="deleteRule(row.id)">
                  <template #reference>
                    <el-button size="small" type="danger">删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="10">
        <el-card shadow="hover">
          <template #header><span>📊 风控记录</span></template>
          <el-table :data="records" stripe size="small" v-loading="recordLoading" max-height="400">
            <el-table-column prop="created_at" label="时间" width="160">
              <template #default="{ row }">
                {{ row.created_at?.slice(5, 19) }}
              </template>
            </el-table-column>
            <el-table-column prop="symbol" label="标的" width="80" />
            <el-table-column prop="action" label="动作" width="70">
              <template #default="{ row }">
                <el-tag :type="row.action === 'block' ? 'danger' : 'warning'" size="small">
                  {{ row.action === 'block' ? '阻止' : '警告' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="message" label="信息" min-width="140">
              <template #default="{ row }">
                <el-tooltip :content="row.message" placement="top">
                  <span>{{ row.message?.slice(0, 20) }}{{ row.message?.length > 20 ? '...' : '' }}</span>
                </el-tooltip>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <!-- 添加/编辑规则对话框 -->
    <el-dialog v-model="dialogVisible" :title="isEditing ? '编辑规则' : '新增规则'" width="550px">
      <el-form :model="form" label-width="110px" size="small">
        <el-form-item label="规则名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="规则类型">
          <el-select v-model="form.rule_type" style="width: 100%">
            <el-option label="单日最大亏损" value="max_daily_loss" />
            <el-option label="最大仓位比例" value="max_position_ratio" />
            <el-option label="最大持仓数量" value="max_position_qty" />
            <el-option label="单笔止损" value="stop_loss" />
            <el-option label="黑名单" value="blacklist" />
            <el-option label="最大挂单数量" value="max_open_orders" />
          </el-select>
        </el-form-item>
        <el-form-item label="触发动作">
          <el-radio-group v-model="form.action">
            <el-radio value="warn">仅警告</el-radio>
            <el-radio value="block">阻止交易</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="是否启用">
          <el-switch v-model="form.is_active" />
        </el-form-item>
        <el-form-item label="规则参数">
          <el-input v-model="form.params" type="textarea" :rows="3" placeholder='{"ratio": 5}' />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRule">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { riskApi } from '@/api'

const loading = ref(false)
const saving = ref(false)
const recordLoading = ref(false)
const rules = ref<any[]>([])
const records = ref<any[]>([])
const dialogVisible = ref(false)
const isEditing = ref(false)

const form = reactive<any>({
  name: '',
  rule_type: 'stop_loss',
  action: 'block',
  is_active: true,
  params: '{}',
  description: '',
})

const ruleTypeLabel = (t: string) => ({
  max_daily_loss: '最大日亏损',
  max_position_ratio: '仓位比例',
  max_position_qty: '持仓数量',
  stop_loss: '止损',
  blacklist: '黑名单',
  max_order_count: '下单次数',
  max_open_orders: '挂单数量',
}[t] || t)

async function loadRules() {
  loading.value = true
  try {
    const res: any = await riskApi.getRules()
    rules.value = res.data || []
  } finally { loading.value = false }
}

async function loadRecords() {
  recordLoading.value = true
  try {
    const res: any = await riskApi.getRecords()
    records.value = res.data || []
  } finally { recordLoading.value = false }
}

function showAddDialog() {
  isEditing.value = false
  Object.assign(form, { name: '', rule_type: 'stop_loss', action: 'block', is_active: true, params: '{}', description: '' })
  dialogVisible.value = true
}

function editRule(row: any) {
  isEditing.value = true
  Object.assign(form, { ...row, params: JSON.stringify(row.params || {}, null, 2) })
  dialogVisible.value = true
}

async function saveRule() {
  saving.value = true
  try {
    const data = { ...form, params: JSON.parse(form.params || '{}') }
    if (isEditing.value) {
      await riskApi.updateRule(form.id, data)
      ElMessage.success('规则已更新')
    } else {
      await riskApi.createRule(data)
      ElMessage.success('规则已创建')
    }
    dialogVisible.value = false
    loadRules()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '保存失败')
  } finally { saving.value = false }
}

async function deleteRule(id: number) {
  try {
    await riskApi.deleteRule(id)
    ElMessage.success('规则已删除')
    loadRules()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || err?.message || '删除失败')
  }
}

onMounted(() => {
  loadRules()
  loadRecords()
})
</script>
