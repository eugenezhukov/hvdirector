<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('clusters.title') }}</h1>
      <el-button @click="load"><el-icon><IconRefresh /></el-icon>{{ t('common.refresh') }}</el-button>
      <el-button type="primary" @click="wizard.open()"><el-icon><IconPlus /></el-icon>{{ t('clusters.add') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items" :empty-text="t('common.noData')" @row-click="(r) => go(r.id)" class="clickable">
        <el-table-column :label="t('common.name')" min-width="180">
          <template #default="{ row }">
            <span class="link">{{ row.name }}</span>
            <div class="muted small">{{ row.cluster_fqdn || row.seed_address }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('clusters.kind')" width="190">
          <template #default="{ row }">{{ row.kind === 'cluster' ? t('clusters.kindCluster') : t('clusters.kindStandalone') }}</template>
        </el-table-column>
        <el-table-column :label="t('common.status')" width="150">
          <template #default="{ row }">
            <el-tooltip :content="row.status_message || '—'" :disabled="!row.status_message">
              <el-tag :type="statusType(row.status)" size="small">{{ t('clusterStatus.' + row.status) }}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column :label="t('clusters.nodes')" width="110">
          <template #default="{ row }">{{ row.hosts_up }} / {{ row.hosts_total }}</template>
        </el-table-column>
        <el-table-column :label="t('clusters.cpu')" width="120" prop="logical_cpus" />
        <el-table-column :label="t('clusters.memory')" width="170">
          <template #default="{ row }">{{ fmtMb(row.memory_free_mb) }} / {{ fmtMb(row.memory_total_mb) }}</template>
        </el-table-column>
        <el-table-column :label="t('vms.ha')" width="70">
          <template #default="{ row }"><el-icon v-if="row.ha_enabled" color="#2e9e5b"><IconCheck /></el-icon></template>
        </el-table-column>
        <el-table-column :label="t('clusters.lastRefresh')" width="170">
          <template #default="{ row }">{{ fmtDate(row.last_refresh_at) }}</template>
        </el-table-column>
      </el-table>
    </div>
    <ClusterWizard ref="wizard" @created="onCreated" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api'
import { useTasks } from '../../stores/tasks'
import ClusterWizard from '../../components/ClusterWizard.vue'
import { fmtDate, fmtMb, statusType } from '../../utils/format'

const { t } = useI18n()
const router = useRouter()
const tasks = useTasks()
const items = ref([])
const loading = ref(false)
const wizard = ref(null)

async function load() {
  loading.value = true
  try {
    items.value = await api.get('/api/provider/clusters')
  } finally {
    loading.value = false
  }
}

function go(id) {
  router.push(`/provider/clusters/${id}`)
}

async function onCreated(res) {
  await load()
  tasks.track(res.task).then(load)
  go(res.cluster.id)
}

onMounted(load)
</script>

<style scoped>
.small { font-size: 12px; }
.clickable :deep(tr) { cursor: pointer; }
</style>
