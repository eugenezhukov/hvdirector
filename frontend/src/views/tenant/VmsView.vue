<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('vms.title') }}</h1>
      <el-input v-model="q" :placeholder="t('common.search')" clearable style="width: 220px">
        <template #prefix><el-icon><IconSearch /></el-icon></template>
      </el-input>
      <el-button @click="load"><el-icon><IconRefresh /></el-icon>{{ t('common.refresh') }}</el-button>
      <el-button v-if="auth.can('vm.create')" type="primary" @click="wizard.open()"><el-icon><IconPlus /></el-icon>{{ t('vms.create') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading && !items.length" :data="filtered" :empty-text="t('vms.noVms')" class="clickable" @row-click="(r) => open(r)">
        <el-table-column :label="t('common.name')" min-width="180" sortable prop="name">
          <template #default="{ row }">
            <span class="link"><b>{{ row.name }}</b></span>
            <div v-if="row.description" class="muted small">{{ row.description }}</div>
            <div v-if="row.status === 'error'" class="danger-text small">{{ row.last_error }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.state')" width="160"><template #default="{ row }"><VmState :vm="row" /></template></el-table-column>
        <el-table-column prop="vcpu" label="vCPU" width="80" sortable />
        <el-table-column :label="t('vms.memory')" width="110" sortable prop="memory_mb"><template #default="{ row }">{{ fmtMb(row.memory_mb) }}</template></el-table-column>
        <el-table-column :label="t('vms.disk')" width="110"><template #default="{ row }">{{ fmtGb(row.disk_gb) }}</template></el-table-column>
        <el-table-column :label="t('vms.ips')" min-width="150"><template #default="{ row }"><span class="mono">{{ row.guest_ips.join(', ') || '—' }}</span></template></el-table-column>
        <el-table-column :label="t('vms.media')" min-width="140" show-overflow-tooltip><template #default="{ row }">{{ row.iso?.name || '—' }}</template></el-table-column>
        <el-table-column width="200" align="right">
          <template #default="{ row }">
            <el-tooltip v-if="row.permissions.includes('vm.console')" :content="t('vms.console')">
              <el-button size="small" circle :disabled="row.status !== 'ready' || row.power_state !== 'Running'" @click.stop="consoleDlg.open(row)">
                <el-icon><IconMonitor /></el-icon>
              </el-button>
            </el-tooltip>
            <VmActionsMenu :vm="row" class="ml" @power="(a) => actions.power(row, a)" @console="consoleDlg.open(row)"
                           @snapshot="actions.snapshot(row)" @delete="actions.remove(row)" />
          </template>
        </el-table-column>
      </el-table>
    </div>
    <VmCreateWizard ref="wizard" @created="onCreated" />
    <ConsoleDialog ref="consoleDlg" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api'
import { useAuth } from '../../stores/auth'
import { useTasks } from '../../stores/tasks'
import { useVmActions } from '../../composables/vmActions'
import VmState from '../../components/VmState.vue'
import VmActionsMenu from '../../components/VmActionsMenu.vue'
import VmCreateWizard from '../../components/VmCreateWizard.vue'
import ConsoleDialog from '../../components/ConsoleDialog.vue'
import { fmtGb, fmtMb } from '../../utils/format'

const { t } = useI18n()
const auth = useAuth()
const tasks = useTasks()
const router = useRouter()
const items = ref([])
const loading = ref(false)
const q = ref('')
const wizard = ref(null)
const consoleDlg = ref(null)
let timer = null

const filtered = computed(() => {
  const s = q.value.toLowerCase()
  return s ? items.value.filter((v) => (v.name + ' ' + v.guest_ips.join(' ') + ' ' + v.description).toLowerCase().includes(s)) : items.value
})

async function load() {
  loading.value = true
  try {
    items.value = await api.get('/api/tenant/vms')
  } finally {
    loading.value = false
  }
}

const actions = useVmActions(load)

function open(vm) {
  router.push(`/tenant/${auth.tenant.slug}/vms/${vm.id}`)
}

async function onCreated(res) {
  await load()
  tasks.track(res.task).then(load)
}

onMounted(() => {
  load()
  timer = setInterval(load, 10000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.clickable :deep(tr) { cursor: pointer; }
.small { font-size: 12px; }
.ml { margin-left: 8px; }
</style>
