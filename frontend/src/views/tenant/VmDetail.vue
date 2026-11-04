<template>
  <div v-if="vm" class="page">
    <div class="page-head">
      <div style="flex: 1">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item :to="base + '/vms'">{{ t('nav.vms') }}</el-breadcrumb-item>
          <el-breadcrumb-item>{{ vm.name }}</el-breadcrumb-item>
        </el-breadcrumb>
        <h1 class="title">{{ vm.name }} <VmState :vm="vm" /></h1>
        <div class="sub">{{ vm.description }}</div>
      </div>
      <template v-if="can('vm.power')">
        <el-button :disabled="!ready || vm.power_state === 'Running'" @click="actions.power(vm, 'start')"><el-icon><IconVideoPlay /></el-icon>{{ t('power.start') }}</el-button>
        <el-button :disabled="!ready || vm.power_state !== 'Running'" @click="actions.power(vm, 'shutdown')"><el-icon><IconSwitchButton /></el-icon>{{ t('power.shutdown') }}</el-button>
        <el-button :disabled="!ready || vm.power_state !== 'Running'" @click="actions.power(vm, 'reboot')"><el-icon><IconRefreshRight /></el-icon>{{ t('power.reboot') }}</el-button>
      </template>
      <el-button v-if="can('vm.console')" type="primary" :disabled="!ready || vm.power_state !== 'Running'" @click="consoleDlg.open(vm)">
        <el-icon><IconMonitor /></el-icon>{{ t('vms.console') }}
      </el-button>
      <VmActionsMenu :vm="vm" @power="(a) => actions.power(vm, a)" @console="consoleDlg.open(vm)" @snapshot="actions.snapshot(vm)" @delete="onDelete" />
    </div>
    <el-alert v-if="vm.status === 'error' && vm.last_error" :title="vm.last_error" type="error" show-icon :closable="false" class="mb" />

    <el-tabs v-model="tab" class="card">
      <el-tab-pane :label="t('vms.general')" name="general">
        <div class="grid-2">
          <div class="kv">
            <div>{{ t('common.name') }}</div><div>{{ vm.name }}</div>
            <div>Hyper-V</div><div class="mono">{{ vm.hv_name }}</div>
            <div>{{ t('common.description') }}</div>
            <div>
              {{ vm.description || '—' }}
              <el-button v-if="can('vm.edit')" link size="small" @click="editDescription"><el-icon><IconEdit /></el-icon></el-button>
            </div>
            <div>{{ t('vms.generation') }}</div><div>{{ vm.generation === 2 ? t('vms.gen2') : t('vms.gen1') }}</div>
            <div v-if="vm.generation === 2">{{ t('vms.secureBoot') }}</div><div v-if="vm.generation === 2">{{ t('vms.secureBootOpts.' + vm.secure_boot) }}</div>
            <div>{{ t('vms.ha') }}</div><div>{{ vm.ha ? t('vms.haYes') : t('common.no') }}</div>
            <div>{{ t('vms.created') }}</div><div>{{ fmtDate(vm.created_at) }}</div>
          </div>
          <div class="kv">
            <div>vCPU</div><div>{{ vm.vcpu }}</div>
            <div>{{ t('vms.memory') }}</div><div>{{ fmtMb(vm.memory_mb) }}</div>
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
        <VmHardware :vm="vm" :ready="ready" :can-edit="can('vm.edit')" :networks="networks"
                    :load-classes="() => api.get('/api/tenant/storage-classes')" :reconfigure="reconfigure" />

        <h4>{{ t('vms.media') }}</h4>
        <div class="media">
          <span>{{ vm.iso?.name || t('vms.noMedia') }}</span>
          <template v-if="can('vm.media')">
            <el-select v-model="isoId" filterable clearable :placeholder="t('vms.insertIso')" style="width: 340px" size="small">
              <el-option v-for="iso in isos" :key="iso.id" :value="iso.id" :label="iso.name + (iso.public ? ` (${t('common.public')})` : '')" />
            </el-select>
            <el-button size="small" :disabled="!ready || !isoId" @click="actions.media(vm, isoId)">{{ t('vms.insertIso') }}</el-button>
            <el-button size="small" :disabled="!ready || !vm.iso" @click="actions.media(vm, null)">{{ t('vms.eject') }}</el-button>
          </template>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('vms.monitoring')" name="monitoring" lazy>
        <VmMetrics :url="`/api/tenant/vms/${vm.id}/metrics`" />
      </el-tab-pane>

      <el-tab-pane :label="`${t('vms.snapshots')} (${vm.snapshots.length})`" name="snap">
        <div class="toolbar">
          <span class="spacer" />
          <el-button v-if="can('vm.snapshot')" size="small" type="primary" :disabled="!ready" @click="actions.snapshot(vm)"><el-icon><IconCamera /></el-icon>{{ t('vms.takeSnapshot') }}</el-button>
        </div>
        <el-table :data="vm.snapshots" size="small" :empty-text="t('common.noData')">
          <el-table-column prop="name" :label="t('common.name')" min-width="200" />
          <el-table-column :label="t('common.status')" width="140">
            <template #default="{ row }"><el-tag size="small" :type="row.status === 'ready' ? 'success' : 'primary'">{{ row.status }}</el-tag></template>
          </el-table-column>
          <el-table-column :label="t('common.created')" width="180"><template #default="{ row }">{{ fmtDate(row.created_at) }}</template></el-table-column>
          <el-table-column v-if="can('vm.snapshot')" width="220" align="right">
            <template #default="{ row }">
              <el-button size="small" :disabled="!ready || row.status !== 'ready'" @click="actions.revert(vm, row)"><el-icon><IconRefreshLeft /></el-icon>{{ t('vms.revert') }}</el-button>
              <el-button size="small" type="danger" plain :disabled="row.status !== 'ready'" @click="actions.deleteSnapshot(vm, row)"><el-icon><IconDelete /></el-icon></el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane v-if="auth.can('user.view')" :label="t('vms.access')" name="access" lazy>
        <div class="form-hint mb">{{ t('vms.accessHint') }}</div>
        <el-table :data="access" size="small" :empty-text="t('common.noData')">
          <el-table-column prop="username" :label="t('common.username')" width="160" />
          <el-table-column prop="full_name" :label="t('common.fullName')" min-width="160" />
          <el-table-column :label="t('users.roles')" min-width="260">
            <template #default="{ row }">
              <el-tag v-for="r in row.roles" :key="r" size="small" class="tag">{{ t('users.roleNames.' + r) }}</el-tag>
              <el-tag v-for="r in row.inherited" :key="'i' + r" size="small" type="info" class="tag">{{ t('users.roleNames.' + r) }} · {{ t('vms.inherited') }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
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

    <ConsoleDialog ref="consoleDlg" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { errorText } from '../../i18n'
import { useAuth } from '../../stores/auth'
import { useVmActions } from '../../composables/vmActions'
import VmState from '../../components/VmState.vue'
import VmActionsMenu from '../../components/VmActionsMenu.vue'
import ConsoleDialog from '../../components/ConsoleDialog.vue'
import VmHardware from '../../components/VmHardware.vue'
import VmMetrics from '../../components/VmMetrics.vue'
import { fmtDate, fmtMb, fmtUptime, statusType, taskKind } from '../../utils/format'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuth()
const vm = ref(null)
const tab = ref('general')
const networks = ref([])
const isos = ref([])
const access = ref([])
const vmTasks = ref([])
const isoId = ref(null)
const consoleDlg = ref(null)
const base = computed(() => `/tenant/${auth.tenant.slug}`)
const ready = computed(() => vm.value && vm.value.status === 'ready')
const can = (p) => (vm.value?.permissions || []).includes(p)
let timer = null
let gone = false

async function load(final) {
  if (gone) return
  try {
    vm.value = await api.get(`/api/tenant/vms/${route.params.id}`)
  } catch (e) {
    if (e.status === 404) {
      gone = true
      router.push(base.value + '/vms')
    }
    return
  }
  if (tab.value === 'tasks') loadTasks()
  if (final && final.kind === 'vm.delete' && final.status === 'success') router.push(base.value + '/vms')
}

const actions = useVmActions(load)

async function onDelete() {
  const res = await actions.remove(vm.value)
  if (res && res.status === 'success') router.push(base.value + '/vms')
}

async function loadTasks() {
  vmTasks.value = await api.get(`/api/tasks?limit=50&target_id=${route.params.id}`)
}

watch(tab, async (v) => {
  if (v === 'tasks') loadTasks()
  if (v === 'access') access.value = await api.get(`/api/tenant/vms/${route.params.id}/access`)
})

async function editDescription() {
  try {
    const { value } = await ElMessageBox.prompt(t('common.description'), vm.value.name, { inputValue: vm.value.description })
    vm.value = await api.patch(`/api/tenant/vms/${vm.value.id}`, { description: value })
  } catch (e) {
    if (e !== 'cancel' && e?.code) ElMessage.error(errorText(e))
  }
}

async function reconfigure(body) {
  return actions.run(api.post(`/api/tenant/vms/${vm.value.id}/reconfigure`, body), vm.value)
}

onMounted(async () => {
  await load()
  if (auth.can('network.view')) networks.value = await api.get('/api/tenant/networks').catch(() => [])
  if (auth.can('iso.view')) isos.value = (await api.get('/api/tenant/isos').catch(() => [])).filter((i) => i.status === 'ready')
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
.ml { margin-left: 10px; }
.media { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.tag { margin: 0 6px 4px 0; }
h4 { margin: 16px 0 8px; font-size: 14px; display: flex; align-items: center; }
</style>
