<template>
  <div class="page">
    <div class="page-head"><h1>{{ t('profile.title') }}</h1></div>
    <div class="grid-2">
      <div class="card">
        <h3>{{ t('profile.account') }}</h3>
        <div class="kv">
          <div>{{ t('common.username') }}</div><div>{{ auth.me.user.username }}</div>
          <div>{{ t('common.fullName') }}</div><div>{{ auth.me.user.full_name || '—' }}</div>
          <div>{{ t('common.organization') }}</div><div>{{ auth.tenant ? `${auth.tenant.name} (${auth.tenant.slug})` : t('nav.providerPortal') }}</div>
          <div v-if="!auth.isProvider">{{ t('users.roles') }}</div>
          <div v-if="!auth.isProvider">
            <el-tag v-for="(r, i) in auth.me.roles" :key="i" size="small" class="role">
              {{ t('users.roleNames.' + r.role) }} · {{ r.vm_id ? 'VM' : t('users.scopeTenant') }}
            </el-tag>
          </div>
          <div>{{ t('common.language') }}</div>
          <div>
            <el-radio-group :model-value="currentLocale" size="small" @change="auth.saveLocale">
              <el-radio-button value="ru">Русский</el-radio-button>
              <el-radio-button value="en">English</el-radio-button>
            </el-radio-group>
          </div>
        </div>
      </div>
      <div class="card">
        <h3>{{ t('profile.changePassword') }}</h3>
        <el-form label-position="top" @submit.prevent="change">
          <el-form-item :label="t('common.currentPassword')"><el-input v-model="cur" type="password" show-password /></el-form-item>
          <el-form-item :label="t('common.newPassword')"><el-input v-model="pw1" type="password" show-password /></el-form-item>
          <el-form-item :label="t('common.repeatPassword')"><el-input v-model="pw2" type="password" show-password /></el-form-item>
          <el-button type="primary" native-type="submit" :loading="busy">{{ t('common.save') }}</el-button>
        </el-form>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { useAuth } from '../stores/auth'
import { currentLocale, errorText } from '../i18n'

const { t } = useI18n()
const auth = useAuth()
const cur = ref('')
const pw1 = ref('')
const pw2 = ref('')
const busy = ref(false)

async function change() {
  if (pw1.value !== pw2.value) return ElMessage.error(t('common.passwordsMismatch'))
  busy.value = true
  try {
    await api.post('/api/auth/password', { current_password: cur.value, new_password: pw1.value })
    ElMessage.success(t('profile.passwordChanged'))
    cur.value = pw1.value = pw2.value = ''
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.role { margin: 0 6px 6px 0; }
</style>
