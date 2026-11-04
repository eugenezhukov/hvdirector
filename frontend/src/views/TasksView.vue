<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('tasks.title') }}</h1>
      <el-select v-model="status" clearable :placeholder="t('common.status')" style="width: 160px" @change="load">
        <el-option v-for="s in ['queued', 'running', 'success', 'error']" :key="s" :value="s" :label="t('tasks.' + s)" />
      </el-select>
      <el-button @click="load"><el-icon><IconRefresh /></el-icon>{{ t('common.refresh') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items" :empty-text="t('tasks.empty')">
        <el-table-column :label="t('tasks.kind')" min-width="170">
          <template #default="{ row }">{{ taskKind(row.kind) }}</template>
        </el-table-column>
        <el-table-column prop="target_name" :label="t('tasks.target')" min-width="140" />
        <el-table-column :label="t('common.status')" width="180">
          <template #default="{ row }">
            <el-progress v-if="row.status === 'running'" :percentage="row.progress" :stroke-width="10" />
            <el-tag v-else :type="statusType(row.status)" size="small">{{ t('tasks.' + row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('tasks.message')" min-width="260">
          <template #default="{ row }">
            <span :class="{ 'danger-text': row.status === 'error' }">{{ row.message }}</span>
            <span v-if="row.result && row.kind === 'cluster.refresh'" class="muted">
              {{ summary(row.result) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="username" :label="t('tasks.user')" width="130" />
        <el-table-column :label="t('tasks.started')" width="170">
          <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column :label="t('tasks.finished')" width="170">
          <template #default="{ row }">{{ fmtDate(row.finished_at) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api'
import { fmtDate, statusType, taskKind } from '../utils/format'

const { t } = useI18n()
const items = ref([])
const loading = ref(false)
const status = ref('')
let timer = null

function summary(r) {
  const parts = []
  if (r.added && r.added.length) parts.push(`${t('clusters.added')}: ${r.added.join(', ')}`)
  if (r.removed && r.removed.length) parts.push(`${t('clusters.removed')}: ${r.removed.join(', ')}`)
  return parts.join('; ')
}

async function load() {
  loading.value = !items.value.length
  try {
    items.value = await api.get(`/api/tasks?limit=200${status.value ? '&status=' + status.value : ''}`)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>
