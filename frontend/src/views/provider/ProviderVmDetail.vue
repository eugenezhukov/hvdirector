<template>
  <div v-if="vm" class="page">
    <div class="page-head">
      <div style="flex: 1">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item to="/provider/vms">{{ t('nav.vms') }}</el-breadcrumb-item>
          <el-breadcrumb-item>{{ vm.hv_name }}</el-breadcrumb-item>
        </el-breadcrumb>
        <h1 class="title">
          {{ vm.name }} <VmState :vm="vm" />
          <el-tag v-if="vm.kind === 'router'" size="small" type="warning">{{ t('nav.edge') }}</el-tag>
        </h1>
        <div class="sub">
          {{ t('vms.organization') }}: <router-link class="link plain" :to="`/provider/tenants/${vm.tenant_id}`">{{ vm.tenant }}</router-link>
          · {{ vm.cluster }} · {{ vm.host || '—' }}
        </div>
      </div>
      <el-button :disabled="!ready || vm.power_state === 'Running'" @click="actions.power(vm, 'start')"><el-icon><IconVideoPlay /></el-icon>{{ t('power.start') }}</el-button>
      <el-button :disabled="!ready || vm.power_state !== 'Running'" @click="actions.power(vm, 'shutdown')"><el-icon><IconSwitchButton /></el-icon>{{ t('power.shutdown') }}</el-button>
      <el-dropdown trigger="click" @command="(a) => actions.power(vm, a)">
        <el-button>{{ t('common.actions') }}<el-icon class="el-icon--right"><IconArrowDown /></el-icon></el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="reboot" :disabled="!ready || vm.power_state !== 'Running'"><el-icon><IconRefreshRight /></el-icon>{{ t('power.reboot') }}</el-dropdown-item>
            <el-dropdown-item command="poweroff" :disabled="!ready || vm.power_state === 'Off'"><el-icon><IconCircleClose /></el-icon>{{ t('power.poweroff') }}</el-dropdown-item>
            <el-dropdown-item command="reset" :disabled="!ready || vm.power_state !== 'Running'"><el-icon><IconRefreshLeft /></el-icon>{{ t('power.reset') }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button type="primary" :disabled="!ready || vm.power_state !== 'Running'" @click="consoleDlg.open(vm)">
        <el-icon><IconMonitor /></el-icon>{{ t('vms.console') }}
      </el-button>
    </div>
    <el-alert :title="t('vms.providerHint')" type="info" show-icon :closable="false" class="mb" />
    <el-alert v-if="vm.status === 'error' && vm.last_error" :title="vm.last_error" type="error" show-icon :closable="false" class="mb" />

    <el-tabs v-model="tab" class="card">
      <el-tab-pane :label="t('vms.general')" name="general">
        <div class="grid-2">
          <div class="kv">
            <div>Hyper-V</div><div class="mono">{{ vm.hv_name }}</div>
            <div>VM ID</div><div class="mono">{{ vm.hv_id || '—' }}</div>
            <div>{{ t('vms.organization') }}</div><div>{{ vm.tenant }}</div>
            <div>{{ t('common.cluster') }} / {{ t('common.host') }}</div><div>{{ vm.cluster }} / {{ vm.host || '—' }}</div>
            <div>{{ t('vms.generation') }}</div><div>{{ vm.generation === 2 ? t('vms.gen2') : t('vms.gen1') }}</div>
            <div>{{ t('vms.ha') }}</div><div>{{ vm.ha ? t('vms.haYes') : t('common.no') }}</div>
            <div>{{ t('vms.created') }}</div><div>{{ fmtDate(vm.created_at) }}</div>
          </div>
          <div class="kv">
            <div>vCPU / {{ t('vms.memory') }}</div><div>{{ vm.vcpu }} / {{ fmtMb(vm.memory_mb) }}</div>
            <div>{{ t('vms.cpuUsage') }}</div><div>{{ vm.power_state === 'Running' ? (vm.cpu_usage ?? 0) + ' %' : '—' }}</div>
            <div>{{ t('vms.uptime') }}</div><div>{{ fmtUptime(vm.uptime_s) }}</div>
            <div>{{ t('vms.ips') }}</div><div class="mono">{{ vm.guest_ips.join(', ') || '—' }}</div>
            <div>{{ t('vms.media') }}</div><div>{{ vm.iso?.name || t('vms.noMedia') }}</div>
            <div>{{ t('clusters.lastRefresh') }}</div>
            <div>{{ fmtDate(vm.last_sync_at) }} <el-button link size="small" :disabled="!ready" @click="actions.refresh(vm)"><el-icon><IconRefresh /></el-icon></el-button></div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('vms.hardware')" name="hw">
        <el-alert v-if="vm.kind !== 'vm'" :title="t('errors.vm_kind_not_supported')" type="info" show-icon :closable="false" />
        <VmHardware :vm="vm" :ready="ready" :can-edit="vm.kind === 'vm'" :networks="vm.options.networks"
                    :load-classes="async () => vm.options.storage_classes" :reconfigure="reconfigure" />
      </el-tab-pane>

      <el-tab-pane :label="t('vms.monitoring')" name="monitoring" lazy>
        <VmMetrics :url="`/api/provider/vms/${vm.id}/metrics`" />
      </el-tab-pane>

      <el-tab-pane :label="t('vms.tasks')" name="tasks" lazy>
        <el-table :data="vmTasks" size="small" :empty-text="t('tasks.empty')">
          <el-table-column :label="t('tasks.kind')" min-width="170"><template #default="{ row }">{{ taskKind(row.kind) }}</template></el-table-column>
          <el-table-column :label="t('common.status')" width="130"><template #default="{ row }"><el-tag size="small" :type="statusType(row.status)">{{ t('tasks.' + row.status) }}</el-tag></template></el-table-column>
          <el-table-column prop="message" :label="t('tasks.message')" min-width="220" show-overflow-tooltip />
          <el-table-column prop="username" :label="t('tasks.user')" width="120" />
          <el-table-column :label="t('tasks.started')" width="170"><template #default="{ row }">{{ fmtDate(row.created_at) }}</template></el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
    <ConsoleDialog ref="consoleDlg" provider />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api'
import { useVmActions } from '../../composables/vmActions'
import ConsoleDialog from '../../components/ConsoleDialog.vue'
import VmHardware from '../../components/VmHardware.vue'
import VmMetrics from '../../components/VmMetrics.vue'
import VmState from '../../components/VmState.vue'
import { fmtDate, fmtMb, fmtUptime, statusType, taskKind } from '../../utils/format'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const vm = ref(null)
const tab = ref('general')
const vmTasks = ref([])
const consoleDlg = ref(null)
const ready = computed(() => vm.value && vm.value.status === 'ready')
let timer = null
let gone = false

async function load() {
  if (gone) return
  try {
    vm.value = await api.get(`/api/provider/vms/${route.params.id}`)
  } catch (e) {
    if (e.status === 404) {
      gone = true
      router.push('/provider/vms')
    }
    return
  }
  if (tab.value === 'tasks') loadTasks()
}
async function loadTasks() {
  vmTasks.value = await api.get(`/api/tasks?limit=50&target_id=${route.params.id}`)
}
watch(tab, (v) => { if (v === 'tasks') loadTasks() })

const actions = useVmActions(load, '/api/provider')
const reconfigure = (body) => actions.run(api.post(`/api/provider/vms/${vm.value.id}/reconfigure`, body), vm.value)

onMounted(async () => {
  await load()
  timer = setInterval(() => load(), 8000)
})
onUnmounted(() => {
  gone = true
  clearInterval(timer)
})
</script>

<style scoped>
.title { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.mb { margin-bottom: 12px; }
</style>
