<template>
  <div class="page">
    <div class="page-head">
      <div style="flex: 1"><h1>{{ t('isos.title') }}</h1><div class="sub">{{ t('isos.hint') }}</div></div>
      <el-button @click="load"><el-icon><IconRefresh /></el-icon>{{ t('common.refresh') }}</el-button>
      <IsoUploader v-if="auth.can('iso.manage')" base-url="/api/tenant/isos" @uploaded="load" @changed="load" />
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items" :empty-text="t('common.noData')">
        <el-table-column :label="t('common.name')" min-width="220">
          <template #default="{ row }"><b>{{ row.name }}</b><div class="mono muted">{{ row.file_name }}</div></template>
        </el-table-column>
        <el-table-column :label="t('common.type')" width="130">
          <template #default="{ row }"><el-tag size="small" :type="row.public ? 'success' : 'info'">{{ row.public ? t('common.public') : t('common.own') }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('common.size')" width="110"><template #default="{ row }">{{ fmtBytes(row.size_bytes) }}</template></el-table-column>
        <el-table-column :label="t('common.status')" width="190">
          <template #default="{ row }">
            <el-progress v-if="row.status === 'uploading'" :percentage="Math.floor((row.uploaded_bytes / row.size_bytes) * 100)" />
            <el-tag v-else :type="statusType(row.status)" size="small">{{ t('isos.statuses.' + row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.created')" width="170"><template #default="{ row }">{{ fmtDate(row.created_at) }}</template></el-table-column>
        <el-table-column v-if="auth.can('iso.manage')" width="70">
          <template #default="{ row }"><el-button v-if="!row.public" link type="danger" @click="remove(row)"><el-icon><IconDelete /></el-icon></el-button></template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { errorText } from '../../i18n'
import { useAuth } from '../../stores/auth'
import IsoUploader from '../../components/IsoUploader.vue'
import { fmtBytes, fmtDate, statusType } from '../../utils/format'

const { t } = useI18n()
const auth = useAuth()
const items = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    items.value = await api.get('/api/tenant/isos')
  } finally {
    loading.value = false
  }
}

async function remove(r) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: r.name }), t('common.confirmTitle'), { type: 'warning' })
    await api.del(`/api/tenant/isos/${r.id}`)
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>
