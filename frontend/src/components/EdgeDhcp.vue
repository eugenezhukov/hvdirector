<template>
  <div v-loading="loading">
    <div class="toolbar">
      <span class="form-hint hint">{{ t('edge.dhcp.hint') }}</span>
      <span class="spacer" />
      <el-button size="small" :disabled="!ready || !nets.some((n) => n.dhcp?.enabled)" @click="openLeases">
        <el-icon><IconList /></el-icon>{{ t('edge.dhcp.leases') }}
      </el-button>
    </div>
    <el-empty v-if="!loading && !nets.length" :description="t('edge.dhcp.noNetworks')" :image-size="80" />
    <el-table v-else :data="nets" size="small" row-key="network.id">
      <el-table-column :label="t('edge.dhcp.network')" min-width="110">
        <template #default="{ row }"><div>{{ row.network.name }}</div><div class="muted small">VLAN {{ row.network.vlan_id }}</div></template>
      </el-table-column>
      <el-table-column :label="t('edge.dhcp.subnet')" min-width="150">
        <template #default="{ row }"><div class="mono">{{ row.network.cidr }}</div><div class="muted mono small">gw {{ row.network.gateway }}</div></template>
      </el-table-column>
      <el-table-column :label="t('edge.dhcp.state')" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.dhcp" size="small" :type="row.dhcp.enabled ? 'success' : 'info'">{{ row.dhcp.enabled ? t('edge.dhcp.on') : t('edge.dhcp.off') }}</el-tag>
          <span v-else class="muted">{{ t('edge.dhcp.notConfigured') }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('edge.dhcp.pool')" min-width="190">
        <template #default="{ row }"><span v-if="row.dhcp" class="mono">{{ row.dhcp.range_start }} – {{ row.dhcp.range_end }}</span><span v-else>—</span></template>
      </el-table-column>
      <el-table-column :label="t('edge.dhcp.leaseTime')" width="90">
        <template #default="{ row }">{{ row.dhcp ? fmtLease(row.dhcp.lease_time) : '—' }}</template>
      </el-table-column>
      <el-table-column :label="t('edge.dhcp.dns')" min-width="130">
        <template #default="{ row }"><span class="mono">{{ row.dhcp?.dns_servers.join(', ') || '—' }}</span></template>
      </el-table-column>
      <el-table-column :label="t('edge.dhcp.bindingsShort')" width="90">
        <template #default="{ row }">
          <el-button link type="primary" @click="openBindings(row)">{{ row.bindings.length }}</el-button>
        </template>
      </el-table-column>
      <el-table-column v-if="canManage" width="120" align="right" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openSettings(row)"><el-icon><IconSetting /></el-icon>{{ t('edge.dhcp.configure') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- settings of one network -->
    <el-dialog v-model="settingsDlg" :title="t('edge.dhcp.settingsTitle', { name: current?.network.name })" width="640px" append-to-body>
      <el-form v-if="current" label-width="180px">
        <el-form-item :label="t('edge.dhcp.state')"><el-switch v-model="form.enabled" :active-text="t('edge.dhcp.enabled')" /></el-form-item>
        <el-form-item :label="t('edge.dhcp.pool')" required>
          <div class="range">
            <el-input v-model="form.range_start" :placeholder="t('edge.dhcp.rangeStart')" class="mono-input" />
            <span>–</span>
            <el-input v-model="form.range_end" :placeholder="t('edge.dhcp.rangeEnd')" class="mono-input" />
          </div>
          <div class="form-hint">{{ t('edge.dhcp.rangeHint', { cidr: current.network.cidr, gateway: current.network.gateway }) }}</div>
        </el-form-item>
        <el-form-item :label="t('edge.dhcp.leaseTime')">
          <el-input-number v-model="form.lease_value" :min="1" :max="form.lease_unit === 60 ? 525600 : form.lease_unit === 3600 ? 8760 : 365" controls-position="right" />
          <el-select v-model="form.lease_unit" style="width: 100px; margin-left: 8px">
            <el-option :value="60" :label="t('edge.dhcp.units.min')" />
            <el-option :value="3600" :label="t('edge.dhcp.units.h')" />
            <el-option :value="86400" :label="t('edge.dhcp.units.d')" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('edge.dhcp.dns')">
          <el-input v-model="form.dns" placeholder="10.0.0.53, 1.1.1.1" />
          <div class="form-hint">{{ t('edge.dhcp.serversHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('edge.dhcp.ntp')"><el-input v-model="form.ntp" placeholder="10.0.0.123" /></el-form-item>
        <el-form-item :label="t('edge.dhcp.domain')">
          <el-input v-model="form.domain_name" placeholder="corp.example" />
          <div class="form-hint">{{ t('edge.dhcp.domainHint') }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="settingsDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="saveSettings">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- static bindings of one network -->
    <el-dialog v-model="bindingsDlg" :title="`${t('edge.dhcp.bindings')}: ${current?.network.name || ''}`" width="820px" append-to-body>
      <template v-if="current">
        <div class="toolbar">
          <span class="muted mono">{{ current.network.cidr }}</span>
          <span v-if="current.dhcp" class="muted">· {{ t('edge.dhcp.outsidePool', { start: current.dhcp.range_start, end: current.dhcp.range_end }) }}</span>
          <span class="spacer" />
          <el-button v-if="canManage" type="primary" size="small" @click="openBinding()"><el-icon><IconPlus /></el-icon>{{ t('edge.dhcp.addBinding') }}</el-button>
        </div>
        <el-table :data="current.bindings" size="small" :empty-text="t('edge.dhcp.noBindings')">
          <el-table-column prop="name" :label="t('edge.dhcp.bindingName')" min-width="130" />
          <el-table-column :label="t('edge.dhcp.mac')" width="160"><template #default="{ row }"><span class="mono">{{ row.mac }}</span></template></el-table-column>
          <el-table-column :label="t('edge.dhcp.address')" width="130"><template #default="{ row }"><span class="mono">{{ row.address }}</span></template></el-table-column>
          <el-table-column :label="t('edge.dhcp.vm')" min-width="110"><template #default="{ row }">{{ vmByMac(current, row.mac) || '—' }}</template></el-table-column>
          <el-table-column prop="description" :label="t('common.description')" min-width="120" />
          <el-table-column v-if="canManage" width="90" align="right">
            <template #default="{ row }">
              <el-button link @click="openBinding(row)"><el-icon><IconEdit /></el-icon></el-button>
              <el-button link type="danger" @click="deleteBinding(row)"><el-icon><IconDelete /></el-icon></el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-dialog>

    <!-- add / edit binding -->
    <el-dialog v-model="bindingDlg" :title="t('edge.dhcp.bindingTitle')" width="600px" append-to-body>
      <el-form v-if="current" label-width="150px">
        <el-form-item :label="t('edge.dhcp.fromVm')">
          <el-select v-model="bindingForm.vm_nic" clearable style="width: 100%" :placeholder="current.vm_nics.length ? '' : t('common.noData')" @change="pickNic">
            <el-option v-for="(n, i) in current.vm_nics" :key="i" :value="i" :disabled="!n.mac"
                       :label="`${n.vm} · ${n.nic} · ${n.mac || t('edge.dhcp.macUnknown')}`" />
          </el-select>
          <div class="form-hint">{{ t('edge.dhcp.fromVmHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('edge.dhcp.bindingName')" required><el-input v-model="bindingForm.name" maxlength="64" /></el-form-item>
        <el-form-item :label="t('edge.dhcp.mac')" required>
          <el-input v-model="bindingForm.mac" placeholder="00:15:5d:00:00:00" class="mono-input" />
          <div class="form-hint">{{ t('edge.dhcp.macHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('edge.dhcp.address')" required>
          <el-input v-model="bindingForm.address" class="mono-input" />
          <div v-if="current.dhcp" class="form-hint">{{ t('edge.dhcp.outsidePool', { start: current.dhcp.range_start, end: current.dhcp.range_end }) }}</div>
        </el-form-item>
        <el-form-item :label="t('common.description')"><el-input v-model="bindingForm.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="bindingDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="saveBinding">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- active leases (read live from the router) -->
    <el-dialog v-model="leasesDlg" :title="t('edge.dhcp.leasesTitle')" width="960px" append-to-body>
      <div class="toolbar">
        <span v-if="leases.fetched_at" class="muted">{{ t('edge.dhcp.fetched', { time: fmtDate(leases.fetched_at) }) }}</span>
        <span class="spacer" />
        <el-button size="small" :loading="leasesLoading" @click="loadLeases"><el-icon><IconRefresh /></el-icon>{{ t('common.refresh') }}</el-button>
      </div>
      <el-alert v-if="leasesError" :title="leasesError" type="error" show-icon :closable="false" class="mb" />
      <el-table v-loading="leasesLoading" :data="leases.leases" size="small" :empty-text="t('common.noData')">
        <el-table-column :label="t('edge.dhcp.network')" min-width="110">
          <template #default="{ row }">{{ row.network }} <span class="muted">· {{ row.vlan_id }}</span></template>
        </el-table-column>
        <el-table-column :label="t('edge.dhcp.address')" width="135">
          <template #default="{ row }">
            <span class="mono">{{ row.address }}</span>
            <el-tag v-if="row.binding" size="small" type="success" class="tag">{{ t('edge.dhcp.static') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('edge.dhcp.mac')" min-width="150">
          <template #default="{ row }">
            <span v-if="row.mac" class="mono">{{ row.mac }}</span>
            <el-tooltip v-else :content="row.client_id" placement="top"><span class="muted">DUID</span></el-tooltip>
          </template>
        </el-table-column>
        <el-table-column :label="t('edge.dhcp.vm')" min-width="110"><template #default="{ row }">{{ row.vm || '—' }}</template></el-table-column>
        <el-table-column :label="t('edge.dhcp.hostname')" min-width="110"><template #default="{ row }">{{ row.hostname || '—' }}</template></el-table-column>
        <el-table-column :label="t('edge.dhcp.expires')" min-width="140">
          <template #default="{ row }">
            <el-tooltip v-if="row.expires_at" :content="fmtDate(row.expires_at)" placement="top"><span>{{ fmtLeft(row.expires_in) }}</span></el-tooltip>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="canManage" width="120" align="right">
          <template #default="{ row }">
            <el-button v-if="!row.binding && row.mac" link type="primary" @click="reserve(row)">{{ t('edge.dhcp.reserve') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="form-hint mt">{{ t('edge.dhcp.leasesHint') }}</div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { errorText } from '../i18n'
import { fmtDate } from '../utils/format'

const props = defineProps({
  canManage: { type: Boolean, default: false },
  ready: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])
const { t } = useI18n()

const nets = ref([])
const loading = ref(true)
const saving = ref(false)
const currentId = ref(null)
const current = ref(null)

async function load() {
  try {
    nets.value = await api.get('/api/tenant/router/dhcp')
    current.value = nets.value.find((n) => n.network.id === currentId.value) || null
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    loading.value = false
  }
}
defineExpose({ load })

async function mutate(promise) {
  saving.value = true
  try {
    const res = await promise
    await load()
    emit('changed')
    return res
  } catch (e) {
    ElMessage.error(errorText(e))
    throw e
  } finally {
    saving.value = false
  }
}

// ---- formatting / IPv4 helpers
const UNITS = [86400, 3600, 60]
function fmtLease(sec) {
  const u = UNITS.find((x) => sec % x === 0) || 60
  const key = { 86400: 'd', 3600: 'h', 60: 'min' }[u]
  return `${Math.round(sec / u)} ${t('edge.dhcp.units.' + key)}`
}
function fmtLeft(sec) {
  if (sec === null || sec === undefined) return '—'
  if (sec <= 0) return t('edge.dhcp.expired')
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.max(1, Math.floor((sec % 3600) / 60))
  const u = (k) => t('edge.dhcp.units.' + k)
  const v = d ? `${d} ${u('d')} ${h} ${u('h')}` : h ? `${h} ${u('h')} ${m} ${u('min')}` : `${m} ${u('min')}`
  return t('edge.dhcp.inSeconds', { v })
}
const ip2n = (s) => s.split('.').reduce((a, o) => a * 256 + Number(o), 0)
const n2ip = (n) => [24, 16, 8, 0].map((b) => Math.floor(n / 2 ** b) % 256).join('.')
const splitList = (v) => (v || '').split(/[\s,;]+/).filter(Boolean)
const normMac = (m) => (m || '').replace(/[^0-9a-fA-F]/g, '').toLowerCase()
const vmByMac = (net, mac) => net.vm_nics.find((n) => n.mac && normMac(n.mac) === normMac(mac))?.vm

function freeAddress(net) {
  // first host address that is not the gateway, not bound and outside the pool
  const [base, len] = net.network.cidr.split('/')
  const first = ip2n(base) + 1
  const last = ip2n(base) + 2 ** (32 - Number(len)) - 2
  const used = new Set(net.bindings.filter((b) => b.id !== bindingForm.id).map((b) => ip2n(b.address)))
  used.add(ip2n(net.network.gateway))
  const lo = net.dhcp ? ip2n(net.dhcp.range_start) : null
  const hi = net.dhcp ? ip2n(net.dhcp.range_end) : null
  for (let a = first; a <= last; a++) {
    if (!used.has(a) && !(lo !== null && a >= lo && a <= hi)) return n2ip(a)
  }
  return ''
}

function select(net) {
  currentId.value = net.network.id
  current.value = net
}

// ---- settings
const settingsDlg = ref(false)
const form = reactive({})
function openSettings(net) {
  select(net)
  const d = net.dhcp
  const lease = d ? d.lease_time : 86400
  const unit = UNITS.find((x) => lease % x === 0) || 60
  Object.assign(form, {
    enabled: d ? d.enabled : true,
    range_start: d ? d.range_start : net.suggested?.range_start || '',
    range_end: d ? d.range_end : net.suggested?.range_end || '',
    lease_value: lease / unit, lease_unit: unit,
    dns: d ? d.dns_servers.join(', ') : splitList(net.network.dns).join(', '),
    ntp: d ? d.ntp_servers.join(', ') : '',
    domain_name: d ? d.domain_name : '',
  })
  settingsDlg.value = true
}
async function saveSettings() {
  const body = { enabled: form.enabled, range_start: form.range_start.trim(), range_end: form.range_end.trim(),
    lease_time: Math.round(form.lease_value * form.lease_unit), dns_servers: splitList(form.dns),
    ntp_servers: splitList(form.ntp), domain_name: form.domain_name.trim() }
  try {
    await mutate(api.put(`/api/tenant/router/dhcp/${current.value.network.id}`, body))
    settingsDlg.value = false
  } catch { /* shown */ }
}

// ---- bindings
const bindingsDlg = ref(false)
const bindingDlg = ref(false)
const bindingForm = reactive({})
function openBindings(net) {
  select(net)
  bindingsDlg.value = true
}
function openBinding(b = null, preset = {}) {
  Object.assign(bindingForm, b ? { id: b.id, vm_nic: null, name: b.name, mac: b.mac, address: b.address, description: b.description }
    : { id: null, vm_nic: null, name: '', mac: '', address: '', description: '', ...preset })
  if (!bindingForm.address) bindingForm.address = freeAddress(current.value)
  bindingDlg.value = true
}
function pickNic(i) {
  const n = current.value.vm_nics[i]
  if (!n) return
  bindingForm.mac = n.mac || ''
  if (!bindingForm.name || current.value.vm_nics.some((x) => x.vm === bindingForm.name)) bindingForm.name = n.vm
}
async function saveBinding() {
  const body = { name: bindingForm.name, mac: bindingForm.mac.trim(), address: bindingForm.address.trim(),
    description: bindingForm.description || '' }
  try {
    await mutate(bindingForm.id ? api.patch(`/api/tenant/router/dhcp/bindings/${bindingForm.id}`, body)
      : api.post(`/api/tenant/router/dhcp/${current.value.network.id}/bindings`, body))
    bindingDlg.value = false
    if (leasesDlg.value) loadLeases()
  } catch { /* shown */ }
}
async function deleteBinding(b) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: b.name }), t('common.confirmTitle'), { type: 'warning' })
  } catch {
    return
  }
  mutate(api.del(`/api/tenant/router/dhcp/bindings/${b.id}`)).catch(() => {})
}

// ---- leases
const leasesDlg = ref(false)
const leasesLoading = ref(false)
const leasesError = ref('')
const leases = ref({ leases: [], fetched_at: null })
async function loadLeases() {
  leasesLoading.value = true
  leasesError.value = ''
  try {
    leases.value = await api.get('/api/tenant/router/dhcp/leases')
  } catch (e) {
    leasesError.value = errorText(e)
  } finally {
    leasesLoading.value = false
  }
}
function openLeases() {
  leases.value = { leases: [], fetched_at: null }
  leasesDlg.value = true
  loadLeases()
}
function reserve(lease) {
  const net = nets.value.find((n) => n.network.id === lease.network_id)
  if (!net) return
  select(net)
  openBinding(null, { name: lease.vm || lease.hostname || '', mac: lease.mac })
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.hint { max-width: 760px; }
.spacer { flex: 1; }
.range { display: flex; align-items: center; gap: 8px; width: 100%; }
.mono-input :deep(input) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.tag { margin-left: 6px; }
.small { font-size: 12px; }
.mb { margin-bottom: 10px; }
.mt { margin-top: 10px; }
</style>
