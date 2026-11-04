<template>
  <div class="page">
    <div class="page-head"><h1>{{ t('provider.dashTitle') }}</h1></div>
    <div v-if="d" class="tiles">
      <div class="tile link" @click="router.push('/provider/clusters')">
        <div class="label">{{ t('provider.clusters') }}</div>
        <div class="value">{{ d.clusters }} <small v-if="d.clusters_error" class="danger-text">({{ d.clusters_error }} ⚠)</small></div>
      </div>
      <div class="tile"><div class="label">{{ t('provider.hostsUp') }}</div><div class="value">{{ d.hosts_up }} <small>/ {{ d.hosts }}</small></div></div>
      <div class="tile link" @click="router.push('/provider/tenants')"><div class="label">{{ t('provider.tenants') }}</div><div class="value">{{ d.tenants }}</div></div>
      <div class="tile link" @click="router.push('/provider/vms')"><div class="label">{{ t('provider.vmsRunning') }}</div><div class="value">{{ d.vms_running }} <small>/ {{ d.vms }}</small></div></div>
    </div>
    <div v-if="d" class="grid-2">
      <div class="card">
        <h3>{{ t('quota.title') }}</h3>
        <QuotaBar :label="t('provider.cpuAlloc')" :used="d.vcpu_allocated" :limit="d.cpu_total || null" />
        <QuotaBar :label="t('provider.memAlloc')" :used="d.memory_allocated_mb" :limit="d.memory_total_mb || null" :format="fmtMb" />
      </div>
      <div class="card">
        <h3>{{ t('provider.failedTasks') }}</h3>
        <el-table :data="d.failed_tasks" size="small" :empty-text="t('tasks.empty')">
          <el-table-column :label="t('tasks.kind')" width="170"><template #default="{ row }">{{ taskKind(row.kind) }}</template></el-table-column>
          <el-table-column prop="target_name" :label="t('tasks.target')" width="130" />
          <el-table-column prop="message" :label="t('tasks.message')" show-overflow-tooltip />
        </el-table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api'
import QuotaBar from '../../components/QuotaBar.vue'
import { fmtMb, taskKind } from '../../utils/format'

const { t } = useI18n()
const router = useRouter()
const d = ref(null)
onMounted(async () => {
  d.value = await api.get('/api/provider/dashboard')
})
</script>
