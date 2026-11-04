<template>
  <el-dialog v-model="visible" :title="form.id ? `${t('common.edit')}: ${form.username}` : t('users.add')" width="720px" :close-on-click-modal="false">
    <el-form label-width="190px">
      <el-form-item :label="t('common.username')" required><el-input v-model="form.username" style="width: 260px" /></el-form-item>
      <el-form-item :label="t('common.fullName')"><el-input v-model="form.full_name" /></el-form-item>
      <el-form-item :label="t('common.email')"><el-input v-model="form.email" /></el-form-item>
      <el-form-item :label="form.id ? t('common.newPassword') : t('common.password')" :required="!form.id">
        <el-input v-model="form.password" type="password" show-password style="width: 260px" autocomplete="new-password" />
      </el-form-item>
      <el-form-item :label="t('common.active')"><el-switch v-model="form.is_active" /></el-form-item>
      <el-form-item v-if="form.id && form.locked" :label="t('common.locked')">
        <el-checkbox v-model="form.unlock">{{ t('common.unlock') }}</el-checkbox>
      </el-form-item>
      <template v-if="withRoles">
        <el-form-item :label="t('users.roles')">
          <div class="roles">
            <div v-for="(r, i) in form.roles" :key="i" class="role-row">
              <el-select v-model="r.role" style="width: 170px">
                <el-option v-for="name in ['tenant_admin', 'audit', 'console']" :key="name" :value="name" :label="t('users.roleNames.' + name)">
                  <span>{{ t('users.roleNames.' + name) }}</span>
                  <span class="muted opt-hint">{{ t('users.roleHints.' + name) }}</span>
                </el-option>
              </el-select>
              <el-select v-model="r.vm_id" style="width: 240px" :placeholder="t('users.scopeTenant')" clearable>
                <el-option :value="''" :label="t('users.scopeTenant')" />
                <el-option v-for="vm in vms" :key="vm.id" :value="vm.id" :label="t('users.scopeVm', { name: vm.name })" />
              </el-select>
              <el-button link type="danger" @click="form.roles.splice(i, 1)"><el-icon><IconDelete /></el-icon></el-button>
            </div>
            <el-button size="small" @click="form.roles.push({ role: 'console', vm_id: '' })"><el-icon><IconPlus /></el-icon>{{ t('users.addRole') }}</el-button>
            <div class="form-hint">
              <div v-for="name in ['tenant_admin', 'audit', 'console']" :key="name"><b>{{ t('users.roleNames.' + name) }}</b> — {{ t('users.roleHints.' + name) }}</div>
            </div>
          </div>
        </el-form-item>
      </template>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    </el-form>
    <template #footer>
      <el-button @click="visible = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="busy" @click="save">{{ t('common.save') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api'
import { errorText } from '../i18n'

// baseUrl: '/api/tenant/users' | '/api/provider/users' | '/api/provider/tenants/<id>/users'
const props = defineProps({ baseUrl: { type: String, required: true }, withRoles: { type: Boolean, default: true },
  vms: { type: Array, default: () => [] } })
const emit = defineEmits(['saved'])
const { t } = useI18n()
const visible = ref(false)
const busy = ref(false)
const error = ref('')
const form = reactive({})

function open(user = null) {
  error.value = ''
  if (user) {
    Object.assign(form, { id: user.id, username: user.username, full_name: user.full_name, email: user.email, password: '',
      is_active: user.is_active, locked: user.locked, unlock: false,
      roles: user.roles.map((r) => ({ role: r.role, vm_id: r.vm_id || '' })) })
  } else {
    Object.assign(form, { id: null, username: '', full_name: '', email: '', password: '', is_active: true, locked: false,
      unlock: false, roles: props.withRoles ? [{ role: 'console', vm_id: '' }] : [] })
  }
  visible.value = true
}

async function save() {
  busy.value = true
  error.value = ''
  try {
    const body = { username: form.username.trim(), full_name: form.full_name, email: form.email, is_active: form.is_active }
    if (form.password) body.password = form.password
    if (props.withRoles) body.roles = form.roles.map((r) => ({ role: r.role, vm_id: r.vm_id || null }))
    if (form.id) {
      if (form.unlock) body.unlock = true
      await api.patch(`${props.baseUrl}/${form.id}`, body)
    } else {
      await api.post(props.baseUrl, body)
    }
    visible.value = false
    emit('saved')
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}

defineExpose({ open })
</script>

<style scoped>
.roles { width: 100%; }
.role-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.opt-hint { font-size: 11px; margin-left: 10px; }
</style>
