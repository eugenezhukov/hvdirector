<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('users.providerTitle') }}</h1>
      <el-button type="primary" @click="dlg.open()"><el-icon><IconPlus /></el-icon>{{ t('users.add') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items">
        <el-table-column prop="username" :label="t('common.username')" width="180" />
        <el-table-column prop="full_name" :label="t('common.fullName')" min-width="180" />
        <el-table-column prop="email" :label="t('common.email')" min-width="180" />
        <el-table-column :label="t('common.status')" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.locked" type="danger" size="small">{{ t('common.locked') }}</el-tag>
            <el-tag v-else :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? t('common.active') : t('common.disabled') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('users.lastLogin')" width="170"><template #default="{ row }">{{ fmtDate(row.last_login_at) }}</template></el-table-column>
        <el-table-column width="100">
          <template #default="{ row }">
            <el-button link @click="dlg.open(row)"><el-icon><IconEdit /></el-icon></el-button>
            <el-button link type="danger" :disabled="row.id === auth.me.user.id" @click="remove(row)"><el-icon><IconDelete /></el-icon></el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <UserDialog ref="dlg" base-url="/api/provider/users" :with-roles="false" @saved="load" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
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
const loading = ref(false)
const dlg = ref(null)

async function load() {
  loading.value = true
  try {
    items.value = await api.get('/api/provider/users')
  } finally {
    loading.value = false
  }
}

async function remove(u) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: u.username }), t('common.confirmTitle'), { type: 'warning' })
    await api.del(`/api/provider/users/${u.id}`)
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>
