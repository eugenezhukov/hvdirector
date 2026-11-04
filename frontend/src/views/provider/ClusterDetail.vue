<template>
  <div v-if="c" class="page">
    <div class="page-head">
      <div style="flex: 1">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item :to="'/provider/clusters'">{{ t('nav.clusters') }}</el-breadcrumb-item>
          <el-breadcrumb-item>{{ c.name }}</el-breadcrumb-item>
        </el-breadcrumb>
        <h1 style="margin-top: 8px">
          {{ c.name }}
          <el-tag :type="statusType(c.status)" size="small">{{ t('clusterStatus.' + c.status) }}</el-tag>
        </h1>
        <div class="sub">{{ c.kind === 'cluster' ? t('clusters.kindCluster') : t('clusters.kindStandalone') }}
          <template v-if="c.cluster_fqdn"> · {{ c.cluster_fqdn }}</template>
          <template v-if="c.cluster_address"> · {{ c.cluster_address }}</template>
          · {{ t('clusters.lastRefresh') }}: {{ fmtDate(c.last_refresh_at) }}</div>
      </div>
      <el-button type="primary" :loading="refreshing" @click="refresh"><el-icon><IconRefresh /></el-icon>{{ t('clusters.refresh') }}</el-button>
      <el-button @click="openEdit"><el-icon><IconEdit /></el-icon>{{ t('clusters.editConnection') }}</el-button>
      <el-button type="danger" plain @click="remove"><el-icon><IconDelete /></el-icon>{{ t('common.delete') }}</el-button>
    </div>
    <el-alert v-if="c.status_message" :title="c.status_message" :type="c.status === 'error' ? 'error' : 'warning'" show-icon :closable="false" class="mb" />

    <el-tabs v-model="tab" class="card">
      <el-tab-pane :label="`${t('clusters.nodes')} (${c.hosts.length})`" name="nodes">
        <el-table :data="c.hosts" size="small">
          <el-table-column :label="t('common.name')" min-width="150">
            <template #default="{ row }">
              <b>{{ row.name }}</b>
              <div class="muted small">{{ row.fqdn }}</div>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.state')" width="190">
            <template #default="{ row }">
              <el-tag :type="stateType(row.state)" size="small">{{ stateText(row.state) }}</el-tag>
              <el-tooltip v-if="row.last_error" :content="row.last_error">
                <el-icon color="#d9822b" class="ml"><IconWarningFilled /></el-icon>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.address')" width="190">
            <template #default="{ row }">
              <span class="mono">{{ row.address }}</span>
              <el-button link size="small" @click="editAddress(row)"><el-icon><IconEdit /></el-icon></el-button>
            </template>
          </el-table-column>
          <el-table-column :label="t('clusters.cpu')" width="100" prop="logical_cpus" />
          <el-table-column :label="t('clusters.memory')" width="170">
            <template #default="{ row }">{{ fmtMb(row.memory_free_mb) }} / {{ fmtMb(row.memory_total_mb) }}</template>
          </el-table-column>
          <el-table-column :label="t('clusters.vmCount')" width="70" prop="vm_count" />
          <el-table-column :label="t('clusters.switches')" min-width="150">
            <template #default="{ row }">
              <el-tag v-for="s in row.switches" :key="s" size="small" :type="s === c.vswitch_name ? 'success' : 'info'" class="sw">{{ s }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="t('clusters.maintenance')" width="120">
            <template #default="{ row }">
              <el-tooltip :content="t('clusters.maintenanceHint')">
                <el-switch :model-value="row.maintenance" :disabled="!row.in_cluster" @change="(v) => setMaintenance(row, v)" />
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column width="70">
            <template #default="{ row }">
              <el-button v-if="!row.in_cluster" link type="danger" @click="deleteHost(row)"><el-icon><IconDelete /></el-icon></el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="form-hint">{{ t('clusters.hostAddressHint') }}</div>
      </el-tab-pane>

      <el-tab-pane :label="`${t('clusters.datastores')} (${c.datastores.length})`" name="ds">
        <div class="toolbar">
          <span class="spacer" />
          <el-button size="small" @click="dsDialog = true"><el-icon><IconPlus /></el-icon>{{ t('clusters.addDatastore') }}</el-button>
        </div>
        <el-table :data="c.datastores" size="small">
          <el-table-column prop="name" :label="t('common.name')" width="160" />
          <el-table-column :label="t('common.path')" min-width="240">
            <template #default="{ row }"><span class="mono">{{ row.path }}</span></template>
          </el-table-column>
          <el-table-column :label="t('common.type')" width="110">
            <template #default="{ row }">{{ t('clusters.datastoreKind.' + row.kind) }}</template>
          </el-table-column>
          <el-table-column :label="t('clusters.capacity')" width="170">
            <template #default="{ row }">{{ fmtGb(row.free_gb) }} / {{ fmtGb(row.capacity_gb) }}</template>
          </el-table-column>
          <el-table-column :label="t('clusters.storageClass')" width="230">
            <template #default="{ row }">
              <el-select :model-value="row.storage_class_id" size="small" clearable :placeholder="t('clusters.notAssigned')"
                         @change="(v) => setClass(row, v)">
                <el-option v-for="sc in classes" :key="sc.id" :value="sc.id" :label="sc.name" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.enabled')" width="90">
            <template #default="{ row }"><el-switch :model-value="row.enabled" @change="(v) => patchDs(row, { enabled: v })" /></template>
          </el-table-column>
          <el-table-column :label="t('clusters.present')" width="90">
            <template #default="{ row }"><el-icon v-if="row.present" color="#2e9e5b"><IconCheck /></el-icon><el-icon v-else color="#d03050"><IconClose /></el-icon></template>
          </el-table-column>
          <el-table-column width="60">
            <template #default="{ row }"><el-button link type="danger" @click="deleteDs(row)"><el-icon><IconDelete /></el-icon></el-button></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="t('clusters.vms')" name="vms" lazy>
        <el-table :data="vms" size="small" :empty-text="t('common.noData')">
          <el-table-column prop="hv_name" label="Hyper-V" min-width="170" />
          <el-table-column prop="tenant" :label="t('common.organization')" width="130" />
          <el-table-column :label="t('common.state')" width="150"><template #default="{ row }"><VmState :vm="row" /></template></el-table-column>
          <el-table-column prop="host" :label="t('common.host')" width="150" />
          <el-table-column prop="vcpu" label="vCPU" width="70" />
          <el-table-column :label="t('vms.memory')" width="100"><template #default="{ row }">{{ fmtMb(row.memory_mb) }}</template></el-table-column>
          <el-table-column :label="t('vms.ha')" width="60"><template #default="{ row }"><el-icon v-if="row.ha" color="#2e9e5b"><IconCheck /></el-icon></template></el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="t('clusters.connection')" name="conn">
        <div class="kv">
          <div>{{ t('clusters.seed') }}</div><div class="mono">{{ c.seed_address }}</div>
          <div>WinRM</div><div>{{ c.winrm_transport }}:{{ c.winrm_port }} · {{ c.winrm_auth }}</div>
          <div>{{ t('wizard.account') }}</div><div>{{ c.username }}</div>
          <div>{{ t('clusters.vswitch') }}</div><div>{{ c.vswitch_name }}</div>
          <div>{{ t('clusters.ha') }}</div><div>{{ c.ha_enabled ? t('common.yes') : t('common.no') }}</div>
          <div>{{ t('clusters.vmFolder') }}</div><div class="mono">{{ c.vm_folder }}</div>
          <div>{{ t('clusters.console', { port: c.console_port }) }}</div><div>{{ c.console_username || t('clusters.consoleCredsHint') }}</div>
          <div>{{ t('clusters.tenants') }}</div><div>{{ c.tenants.map((x) => x.slug).join(', ') || '—' }}</div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- Edit connection -->
    <el-dialog v-model="editDialog" :title="t('clusters.editConnection')" width="640px">
      <el-form label-width="230px">
        <el-form-item :label="t('wizard.displayName')"><el-input v-model="edit.name" /></el-form-item>
        <el-form-item :label="t('clusters.seed')"><el-input v-model="edit.seed_address" /></el-form-item>
        <el-form-item :label="t('wizard.account')"><el-input v-model="edit.username" /></el-form-item>
        <el-form-item :label="t('common.password')"><el-input v-model="edit.password" type="password" show-password :placeholder="'••••••'" /></el-form-item>
        <el-form-item :label="t('clusters.winrmTransport')">
          <el-select v-model="edit.winrm_transport" style="width: 110px"><el-option value="http" label="HTTP" /><el-option value="https" label="HTTPS" /></el-select>
          <el-input-number v-model="edit.winrm_port" :min="1" :max="65535" controls-position="right" style="margin-left: 8px; width: 120px" />
        </el-form-item>
        <el-form-item :label="t('clusters.winrmAuth')">
          <el-select v-model="edit.winrm_auth" style="width: 240px">
            <el-option value="ntlm" label="NTLM" /><el-option value="negotiate" label="Negotiate" />
            <el-option value="kerberos" label="Kerberos" /><el-option value="credssp" label="CredSSP" /><el-option value="basic" label="Basic (HTTPS)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('clusters.vswitch')"><el-input v-model="edit.vswitch_name" /></el-form-item>
        <el-form-item v-if="c.kind === 'cluster'" :label="t('clusters.ha')"><el-switch v-model="edit.ha_enabled" /></el-form-item>
        <el-form-item :label="t('clusters.consoleCreds')">
          <el-input v-model="edit.console_username" :placeholder="t('common.username')" style="width: 48%" />
          <el-input v-model="edit.console_password" type="password" show-password :placeholder="t('common.password')" style="width: 48%; margin-left: 4%" />
        </el-form-item>
        <el-form-item :label="t('clusters.console', { port: edit.console_port }) + ' — ' + t('clusters.port')">
          <el-input-number v-model="edit.console_port" :min="1" :max="65535" controls-position="right" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveEdit">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- Add datastore -->
    <el-dialog v-model="dsDialog" :title="t('clusters.addDatastore')" width="560px">
      <el-form label-width="160px">
        <el-form-item :label="t('common.name')"><el-input v-model="newDs.name" /></el-form-item>
        <el-form-item :label="t('common.path')"><el-input v-model="newDs.path" placeholder="C:\ClusterStorage\Volume3 / \\sofs\vms" /></el-form-item>
        <el-form-item :label="t('common.type')">
          <el-radio-group v-model="newDs.kind">
            <el-radio-button value="csv">CSV</el-radio-button><el-radio-button value="smb">SMB3</el-radio-button><el-radio-button value="local">Local</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('clusters.storageClass')">
          <el-select v-model="newDs.storage_class_id" clearable><el-option v-for="sc in classes" :key="sc.id" :value="sc.id" :label="sc.name" /></el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dsDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="addDs">{{ t('common.add') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { api } from '../../api'
import { errorText } from '../../i18n'
import { useTasks } from '../../stores/tasks'
import VmState from '../../components/VmState.vue'
import { fmtDate, fmtGb, fmtMb, stateText, stateType, statusType } from '../../utils/format'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const tasks = useTasks()
const c = ref(null)
const vms = ref([])
const classes = ref([])
const tab = ref('nodes')
const refreshing = ref(false)
const editDialog = ref(false)
const dsDialog = ref(false)
const edit = reactive({})
const newDs = reactive({ name: '', path: '', kind: 'csv', storage_class_id: null })
const id = route.params.id

async function load() {
  c.value = await api.get(`/api/provider/clusters/${id}`)
}

async function loadVms() {
  vms.value = await api.get(`/api/provider/clusters/${id}/vms`)
}

watch(tab, (v) => v === 'vms' && loadVms())

async function act(fn, ok = true) {
  try {
    await fn()
    if (ok) ElMessage.success(t('common.saved'))
    await load()
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function refresh() {
  refreshing.value = true
  try {
    const task = await api.post(`/api/provider/clusters/${id}/refresh`)
    const final = await tasks.track(task, { notify: false })
    await load()
    if (final.status === 'success') {
      const r = final.result || {}
      const lines = []
      if (r.added?.length) lines.push(`${t('clusters.added')}: ${r.added.join(', ')}`)
      if (r.removed?.length) lines.push(`${t('clusters.removed')}: ${r.removed.join(', ')}`)
      if (r.returned?.length) lines.push(`${t('clusters.returned')}: ${r.returned.join(', ')}`)
      if (!lines.length) lines.push(t('clusters.noChanges'))
      if (r.warnings?.length) lines.push(`${t('clusters.warnings')}: ${r.warnings.join('; ')}`)
      ElNotification({ title: t('clusters.refreshDone'), message: lines.join('\n'), type: r.added?.length || r.removed?.length ? 'warning' : 'success', duration: 8000, customClass: 'pre' })
    } else {
      ElNotification({ title: t('tasks.failed'), message: final.message, type: 'error', duration: 0 })
    }
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    refreshing.value = false
  }
}

function openEdit() {
  Object.assign(edit, {
    name: c.value.name, seed_address: c.value.seed_address, username: c.value.username, password: '',
    winrm_transport: c.value.winrm_transport, winrm_port: c.value.winrm_port, winrm_auth: c.value.winrm_auth,
    vswitch_name: c.value.vswitch_name, ha_enabled: c.value.ha_enabled, console_username: c.value.console_username,
    console_password: '', console_port: c.value.console_port,
  })
  editDialog.value = true
}

async function saveEdit() {
  const body = { ...edit }
  if (!body.password) delete body.password
  if (!body.console_password) delete body.console_password
  await act(() => api.patch(`/api/provider/clusters/${id}`, body))
  editDialog.value = false
}

async function remove() {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: c.value.name }), t('common.confirmTitle'), { type: 'warning', confirmButtonText: t('common.yesDelete') })
  } catch {
    return
  }
  try {
    await api.del(`/api/provider/clusters/${id}`)
    router.push('/provider/clusters')
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function editAddress(h) {
  try {
    const { value } = await ElMessageBox.prompt(t('clusters.hostAddressHint'), h.name, { inputValue: h.address })
    await act(() => api.patch(`/api/provider/hosts/${h.id}`, { address: value }))
  } catch {
    /* cancelled */
  }
}

const setMaintenance = (h, v) => act(() => api.patch(`/api/provider/hosts/${h.id}`, { maintenance: v }))

async function deleteHost(h) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: h.name }), t('common.confirmTitle'), { type: 'warning' })
  } catch {
    return
  }
  await act(() => api.del(`/api/provider/hosts/${h.id}`))
}

const setClass = (d, v) => act(() => api.patch(`/api/provider/datastores/${d.id}`, v ? { storage_class_id: v } : { clear_storage_class: true }))
const patchDs = (d, body) => act(() => api.patch(`/api/provider/datastores/${d.id}`, body))

async function deleteDs(d) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: d.path }), t('common.confirmTitle'), { type: 'warning' })
  } catch {
    return
  }
  await act(() => api.del(`/api/provider/datastores/${d.id}`))
}

async function addDs() {
  await act(() => api.post(`/api/provider/clusters/${id}/datastores`, { ...newDs }))
  dsDialog.value = false
  Object.assign(newDs, { name: '', path: '', kind: 'csv', storage_class_id: null })
}

onMounted(async () => {
  await load()
  classes.value = await api.get('/api/provider/storage-classes')
})
</script>

<style scoped>
.mb { margin-bottom: 12px; }
.ml { margin-left: 6px; vertical-align: middle; }
.small { font-size: 12px; }
.sw { margin: 0 4px 4px 0; }
h1 { display: flex; align-items: center; gap: 10px; }
</style>
<style>
.el-notification.pre .el-notification__content p { white-space: pre-line; }
</style>
