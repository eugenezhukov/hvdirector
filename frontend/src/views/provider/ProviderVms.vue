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
        <el-table-column prop="hv_name" label="Hyper-V" min-width="170" sortable>
          <template #default="{ row }"><router-link class="link plain" :to="`/provider/vms/${row.id}`">{{ row.hv_name }}</router-link></template>
        </el-table-column>
        <el-table-column prop="tenant" :label="t('common.organization')" width="140" sortable />
        <el-table-column prop="cluster" :label="t('common.cluster')" width="150" sortable />
        <el-table-column prop="host" :label="t('common.host')" width="150" sortable />
        <el-table-column :label="t('common.state')" width="150"><template #default="{ row }"><VmState :vm="row" /></template></el-table-column>
        <el-table-column prop="vcpu" label="vCPU" width="70" />
        <el-table-column :label="t('vms.memory')" width="100"><template #default="{ row }">{{ fmtMb(row.memory_mb) }}</template></el-table-column>
        <el-table-column :label="t('vms.disk')" width="100"><template #default="{ row }">{{ fmtGb(row.disk_gb) }}</template></el-table-column>
        <el-table-column :label="t('vms.ha')" width="60"><template #default="{ row }"><el-icon v-if="row.ha" color="#2e9e5b"><IconCheck /></el-icon></template></el-table-column>
        <el-table-column :label="t('vms.ips')" min-width="140"><template #default="{ row }"><span class="mono">{{ row.guest_ips.join(', ') }}</span></template></el-table-column>
        <el-table-column width="120" align="right" fixed="right">
          <template #default="{ row }">
            <el-dropdown trigger="click" @command="(a) => onCommand(row, a)">
              <el-button size="small">{{ t('common.actions') }}<el-icon class="el-icon--right"><IconArrowDown /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="open"><el-icon><IconView /></el-icon>{{ t('common.open') }}</el-dropdown-item>
                  <el-dropdown-item command="start" divided :disabled="row.status !== 'ready' || row.power_state === 'Running'"><el-icon><IconVideoPlay /></el-icon>{{ t('power.start') }}</el-dropdown-item>
                  <el-dropdown-item command="shutdown" :disabled="row.status !== 'ready' || row.power_state !== 'Running'"><el-icon><IconSwitchButton /></el-icon>{{ t('power.shutdown') }}</el-dropdown-item>
                  <el-dropdown-item command="reboot" :disabled="row.status !== 'ready' || row.power_state !== 'Running'"><el-icon><IconRefreshRight /></el-icon>{{ t('power.reboot') }}</el-dropdown-item>
                  <el-dropdown-item command="poweroff" :disabled="row.status !== 'ready' || row.power_state === 'Off'"><el-icon><IconCircleClose /></el-icon>{{ t('power.poweroff') }}</el-dropdown-item>
                  <el-dropdown-item command="reset" :disabled="row.status !== 'ready' || row.power_state !== 'Running'"><el-icon><IconRefreshLeft /></el-icon>{{ t('power.reset') }}</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api'
import { useVmActions } from '../../composables/vmActions'
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
const router = useRouter()
const actions = useVmActions(() => load(), '/api/provider')
function onCommand(row, cmd) {
  if (cmd === 'open') router.push(`/provider/vms/${row.id}`)
  else actions.power(row, cmd)
}
onMounted(load)
</script>
