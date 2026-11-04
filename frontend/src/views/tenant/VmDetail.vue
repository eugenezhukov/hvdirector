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
        <h4>
          vCPU / {{ t('vms.memory') }}: {{ vm.vcpu }} / {{ fmtMb(vm.memory_mb) }}
          <el-tooltip v-if="can('vm.edit')" :content="t('vms.mustBeOff')">
            <el-button size="small" :disabled="!ready || vm.power_state !== 'Off'" @click="openCompute" class="ml">{{ t('vms.editCompute') }}</el-button>
          </el-tooltip>
        </h4>

        <h4>{{ t('vms.disks') }}
          <el-button v-if="can('vm.edit')" size="small" :disabled="!ready" class="ml" @click="openAddDisk"><el-icon><IconPlus /></el-icon>{{ t('vms.addDisk') }}</el-button>
        </h4>
        <el-table :data="vm.disks" size="small">
          <el-table-column :label="'#'" width="60" prop="position" />
          <el-table-column :label="t('common.size')" width="120"><template #default="{ row }">{{ fmtGb(row.size_gb) }}</template></el-table-column>
          <el-table-column prop="storage_class" :label="t('vms.storageClass')" min-width="160" />
        </el-table>

        <h4>{{ t('vms.nics') }}
          <el-button v-if="can('vm.edit')" size="small" :disabled="!ready" class="ml" @click="addNic"><el-icon><IconPlus /></el-icon>{{ t('vms.addNic') }}</el-button>
        </h4>
        <el-table :data="vm.nics" size="small">
          <el-table-column prop="name" :label="t('common.name')" width="90" />
          <el-table-column :label="t('nav.networks')" min-width="240">
            <template #default="{ row }">
              <el-select v-if="can('vm.edit')" :model-value="row.network_id" clearable size="small" :placeholder="t('vms.disconnected')"
                         :disabled="!ready" style="width: 260px" @change="(v) => changeNic(row, v)">
                <el-option v-for="n in networks" :key="n.id" :value="n.id" :label="`${n.name} (VLAN ${n.vlan_id})`" />
              </el-select>
              <span v-else>{{ row.network ? `${row.network} (VLAN ${row.vlan_id})` : t('vms.disconnected') }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('vms.mac')" width="170"><template #default="{ row }"><span class="mono">{{ row.mac || '—' }}</span></template></el-table-column>
          <el-table-column width="60">
            <template #default="{ row }">
              <el-button v-if="can('vm.edit')" link type="danger" :disabled="!ready" @click="removeNic(row)"><el-icon><IconDelete /></el-icon></el-button>
            </template>
          </el-table-column>
        </el-table>

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

    <el-dialog v-model="computeDlg" :title="t('vms.editCompute')" width="420px">
      <el-form label-width="140px">
        <el-form-item label="vCPU"><el-input-number v-model="compute.vcpu" :min="1" :max="64" /></el-form-item>
        <el-form-item :label="t('vmWizard.memoryGb')"><el-input-number v-model="compute.memory_gb" :min="0.5" :step="1" :precision="1" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="computeDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveCompute">{{ t('common.apply') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="diskDlg" :title="t('vms.addDisk')" width="480px">
      <el-form label-width="160px">
        <el-form-item :label="t('vmWizard.diskSize')"><el-input-number v-model="newDisk.size_gb" :min="1" :max="65536" /></el-form-item>
        <el-form-item :label="t('vms.storageClass')">
          <el-select v-model="newDisk.storage_class_id">
            <el-option v-for="c in classes" :key="c.id" :value="c.id" :disabled="!c.available"
                       :label="`${c.name} — ${c.limit_gb === null ? t('quota.unlimited') : t('vmWizard.available', { n: fmtGb(c.limit_gb - c.used_gb) })}`" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="diskDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!newDisk.storage_class_id" @click="saveDisk">{{ t('common.add') }}</el-button>
      </template>
    </el-dialog>

    <ConsoleDialog ref="consoleDlg" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
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
import { fmtDate, fmtGb, fmtMb, fmtUptime, statusType, taskKind } from '../../utils/format'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuth()
const vm = ref(null)
const tab = ref('general')
const networks = ref([])
const isos = ref([])
const classes = ref([])
const access = ref([])
const vmTasks = ref([])
const isoId = ref(null)
const consoleDlg = ref(null)
const computeDlg = ref(false)
const diskDlg = ref(false)
const compute = reactive({ vcpu: 1, memory_gb: 1 })
const newDisk = reactive({ size_gb: 50, storage_class_id: null })
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

function openCompute() {
  compute.vcpu = vm.value.vcpu
  compute.memory_gb = vm.value.memory_mb / 1024
  computeDlg.value = true
}

async function reconfigure(body) {
  return actions.run(api.post(`/api/tenant/vms/${vm.value.id}/reconfigure`, body), vm.value)
}

async function saveCompute() {
  computeDlg.value = false
  await reconfigure({ vcpu: compute.vcpu, memory_mb: Math.round(compute.memory_gb * 1024) })
}

async function openAddDisk() {
  classes.value = await api.get('/api/tenant/storage-classes')
  newDisk.storage_class_id = classes.value.find((c) => c.available)?.id || null
  diskDlg.value = true
}

async function saveDisk() {
  diskDlg.value = false
  await reconfigure({ add_disks: [{ ...newDisk }] })
}

const addNic = () => reconfigure({ nics: [{ op: 'add', network_id: networks.value[0]?.id || null }] })
const changeNic = (nic, networkId) => reconfigure({ nics: [{ op: 'set', nic_id: nic.id, network_id: networkId || null }] })

async function removeNic(nic) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: nic.name }), t('common.confirmTitle'), { type: 'warning' })
  } catch {
    return
  }
  await reconfigure({ nics: [{ op: 'remove', nic_id: nic.id }] })
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
