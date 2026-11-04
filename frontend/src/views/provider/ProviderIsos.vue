<template>
  <div class="page">
    <div class="page-head">
      <div style="flex: 1"><h1>{{ t('isos.title') }}</h1><div class="sub">{{ t('isos.publicHint') }}</div></div>
      <el-button @click="load"><el-icon><IconRefresh /></el-icon>{{ t('common.refresh') }}</el-button>
      <IsoUploader base-url="/api/provider/isos" @uploaded="load" @changed="load" />
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items" :empty-text="t('common.noData')">
        <el-table-column :label="t('common.name')" min-width="200">
          <template #default="{ row }"><b>{{ row.name }}</b><div class="mono muted">{{ row.file_name }}</div></template>
        </el-table-column>
        <el-table-column :label="t('isos.tenant')" width="150">
          <template #default="{ row }">
            <el-tag v-if="row.public" size="small" type="success">{{ t('common.public') }}</el-tag>
            <span v-else class="mono">{{ row.tenant }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.size')" width="110"><template #default="{ row }">{{ fmtBytes(row.size_bytes) }}</template></el-table-column>
        <el-table-column :label="t('common.status')" width="180">
          <template #default="{ row }">
            <el-progress v-if="row.status === 'uploading'" :percentage="Math.floor((row.uploaded_bytes / row.size_bytes) * 100)" />
            <el-tag v-else :type="statusType(row.status)" size="small">{{ t('isos.statuses.' + row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.created')" width="170"><template #default="{ row }">{{ fmtDate(row.created_at) }}</template></el-table-column>
        <el-table-column width="70">
          <template #default="{ row }"><el-button link type="danger" @click="remove(row)"><el-icon><IconDelete /></el-icon></el-button></template>
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
import IsoUploader from '../../components/IsoUploader.vue'
import { fmtBytes, fmtDate, statusType } from '../../utils/format'

const { t } = useI18n()
const items = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    items.value = await api.get('/api/provider/isos')
  } finally {
    loading.value = false
  }
}

async function remove(r) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: r.name }), t('common.confirmTitle'), { type: 'warning' })
    await api.del(`/api/provider/isos/${r.id}`)
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>
