<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('nav.vms') }}</h1>
      <el-input v-model="q" :placeholder="t('common.search')" clearable style="width: 240px">
        <template #prefix><el-icon><IconSearch /></el-icon></template>
      </el-input>
      <el-button @click="load"><el-icon><IconRefresh /></el-icon>{{ t('common.refresh') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="filtered" size="small" :empty-text="t('common.noData')">
        <el-table-column prop="hv_name" label="Hyper-V" min-width="170" sortable />
        <el-table-column prop="tenant" :label="t('common.organization')" width="140" sortable />
        <el-table-column prop="cluster" :label="t('common.cluster')" width="150" sortable />
        <el-table-column prop="host" :label="t('common.host')" width="150" sortable />
        <el-table-column :label="t('common.state')" width="150"><template #default="{ row }"><VmState :vm="row" /></template></el-table-column>
        <el-table-column prop="vcpu" label="vCPU" width="70" />
        <el-table-column :label="t('vms.memory')" width="100"><template #default="{ row }">{{ fmtMb(row.memory_mb) }}</template></el-table-column>
        <el-table-column :label="t('vms.disk')" width="100"><template #default="{ row }">{{ fmtGb(row.disk_gb) }}</template></el-table-column>
        <el-table-column :label="t('vms.ha')" width="60"><template #default="{ row }"><el-icon v-if="row.ha" color="#2e9e5b"><IconCheck /></el-icon></template></el-table-column>
        <el-table-column :label="t('vms.ips')" min-width="140"><template #default="{ row }"><span class="mono">{{ row.guest_ips.join(', ') }}</span></template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../api'
import VmState from '../../components/VmState.vue'
import { fmtGb, fmtMb } from '../../utils/format'

const { t } = useI18n()
const items = ref([])
const loading = ref(false)
const q = ref('')
const filtered = computed(() => {
  const s = q.value.toLowerCase()
  return s ? items.value.filter((v) => [v.hv_name, v.tenant, v.host, v.cluster].join(' ').toLowerCase().includes(s)) : items.value
})

async function load() {
  loading.value = true
  try {
    items.value = await api.get('/api/provider/vms')
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>
