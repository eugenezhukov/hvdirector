<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('users.title') }}</h1>
      <el-button v-if="canManage" type="primary" @click="dlg.open()"><el-icon><IconPlus /></el-icon>{{ t('users.add') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items">
        <el-table-column prop="username" :label="t('common.username')" width="160" />
        <el-table-column prop="full_name" :label="t('common.fullName')" min-width="170" />
        <el-table-column :label="t('users.roles')" min-width="300">
          <template #default="{ row }">
            <el-tag v-for="r in row.roles" :key="r.id" size="small" :type="r.vm_id ? 'warning' : 'primary'" class="role">
              {{ t('users.roleNames.' + r.role) }} · {{ r.vm_id ? t('users.scopeVm', { name: r.vm_name }) : t('users.scopeTenant') }}
            </el-tag>
            <span v-if="!row.roles.length" class="muted">{{ t('users.noRoles') }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.status')" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.locked" type="danger" size="small">{{ t('common.locked') }}</el-tag>
            <el-tag v-else :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? t('common.active') : t('common.disabled') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('users.lastLogin')" width="170"><template #default="{ row }">{{ fmtDate(row.last_login_at) }}</template></el-table-column>
        <el-table-column v-if="canManage" width="100">
          <template #default="{ row }">
            <el-button link @click="dlg.open(row)"><el-icon><IconEdit /></el-icon></el-button>
            <el-button link type="danger" :disabled="row.id === auth.me.user.id" @click="remove(row)"><el-icon><IconDelete /></el-icon></el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <UserDialog ref="dlg" base-url="/api/tenant/users" :vms="vms" @saved="load" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { errorText } from '../../i18n'
import { useAuth } from '../../stores/auth'
import UserDialog from '../../components/UserDialog.vue'
import { fmtDate } from '../../utils/format'

const { t } = useI18n()
const auth = useAuth()
const items = ref([])
const vms = ref([])
const loading = ref(false)
const dlg = ref(null)
const canManage = computed(() => auth.can('user.manage'))

async function load() {
  loading.value = true
  try {
    ;[items.value, vms.value] = await Promise.all([api.get('/api/tenant/users'), api.get('/api/tenant/vms')])
  } finally {
    loading.value = false
  }
}

async function remove(u) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: u.username }), t('common.confirmTitle'), { type: 'warning' })
    await api.del(`/api/tenant/users/${u.id}`)
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>

<style scoped>
.role { margin: 0 6px 4px 0; }
</style>
