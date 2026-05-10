<template>
  <el-form label-width="100px" size="small">
    <el-form-item label="启用调度">
      <el-switch v-model="localConfig.enabled" />
    </el-form-item>
    <template v-if="localConfig.enabled">
      <el-form-item label="调度类型">
        <el-radio-group v-model="localConfig.type">
          <el-radio label="interval">间隔执行</el-radio>
          <el-radio label="cron">定时执行</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="间隔分钟" v-if="localConfig.type === 'interval'">
        <el-input-number v-model="localConfig.interval_minutes" :min="1" :max="1440" />
      </el-form-item>
      <el-form-item label="Cron 表达式" v-if="localConfig.type === 'cron'">
        <el-input v-model="localConfig.cron_expression" placeholder="例如: 0 9 * * 1-5" />
        <div style="font-size: 12px; color: #999; margin-top: 4px">
          分 时 日 月 周 — 例如每天9点: 0 9 * * *
        </div>
      </el-form-item>
      <el-form-item label="活跃时段">
        <el-time-picker
          v-model="activeHours"
          is-range
          range-separator="至"
          start-placeholder="开始"
          end-placeholder="结束"
          format="HH:mm"
          value-format="HH:mm"
          style="width: 100%"
        />
      </el-form-item>
      <el-form-item label="活跃日期">
        <el-checkbox-group v-model="localConfig.active_days">
          <el-checkbox label="Mon">周一</el-checkbox>
          <el-checkbox label="Tue">周二</el-checkbox>
          <el-checkbox label="Wed">周三</el-checkbox>
          <el-checkbox label="Thu">周四</el-checkbox>
          <el-checkbox label="Fri">周五</el-checkbox>
          <el-checkbox label="Sat">周六</el-checkbox>
          <el-checkbox label="Sun">周日</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
    </template>
  </el-form>
</template>

<script setup lang="ts">
import { reactive, computed, watch } from 'vue'

const props = defineProps<{ modelValue: any }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: any): void }>()

const localConfig = reactive({
  enabled: props.modelValue?.enabled || false,
  type: props.modelValue?.type || 'interval',
  interval_minutes: props.modelValue?.interval_minutes || 15,
  cron_expression: props.modelValue?.cron_expression || '0 9 * * *',
  active_hours: props.modelValue?.active_hours || { start: '09:30', end: '15:00' },
  active_days: props.modelValue?.active_days || ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
})

const activeHours = computed({
  get: () => {
    const h = localConfig.active_hours || {}
    return [h.start || '09:00', h.end || '17:00']
  },
  set: (val: string[]) => {
    localConfig.active_hours = { start: val[0] || '09:00', end: val[1] || '17:00' }
  },
})

watch(localConfig, () => {
  emit('update:modelValue', { ...localConfig })
}, { deep: true })
</script>
