<template>
  <el-dialog v-model="visible" title="修改密码" width="400px" :close-on-click-modal="false">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="90px" label-position="right">
      <el-form-item label="当前密码" prop="current_password">
        <el-input v-model="form.current_password" type="password" show-password placeholder="请输入当前密码" />
      </el-form-item>
      <el-form-item label="新密码" prop="new_password">
        <el-input v-model="form.new_password" type="password" show-password placeholder="至少 6 位" />
      </el-form-item>
      <el-form-item label="确认密码" prop="confirm_password">
        <el-input v-model="form.confirm_password" type="password" show-password placeholder="再次输入新密码" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="handleSubmit">确认修改</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { authApi } from '@/api'

const visible = ref(false)
const submitting = ref(false)
const formRef = ref<FormInstance>()

const form = reactive({
  current_password: '',
  new_password: '',
  confirm_password: '',
})

const rules: FormRules = {
  current_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirm_password: [
    { required: true, message: '请确认新密码', trigger: 'blur' },
    { validator: (_rule: any, value: string, callback: any) => {
      if (value !== form.new_password) callback(new Error('两次密码不一致'))
      else callback()
    }, trigger: 'blur' },
  ],
}

function open() {
  visible.value = true
  form.current_password = ''
  form.new_password = ''
  form.confirm_password = ''
}

async function handleSubmit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    await authApi.changePassword({
      current_password: form.current_password,
      new_password: form.new_password,
    })
    ElMessage.success('密码修改成功')
    visible.value = false
  } catch (err: any) {
    const msg = err?.response?.data?.detail || err?.response?.data?.message || '修改失败'
    ElMessage.error(msg)
  } finally {
    submitting.value = false
  }
}

defineExpose({ open })
</script>
