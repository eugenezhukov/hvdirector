<template>
  <el-dialog v-model="visible" :title="t('wizard.title')" width="900px" top="5vh" :close-on-click-modal="false" destroy-on-close>
    <el-steps :active="step" finish-status="success" align-center class="steps">
      <el-step :title="t('wizard.step1')" />
      <el-step :title="t('wizard.step2')" />
      <el-step :title="t('wizard.step3')" />
      <el-step :title="t('wizard.step4')" />
    </el-steps>

    <!-- Step 1: connection -->
    <el-form v-if="step === 0" label-width="230px" class="form">
      <el-form-item :label="t('wizard.address')" required>
        <el-input v-model="conn.address" placeholder="10.0.0.11 / hv01.corp.local" />
        <div class="form-hint">{{ t('wizard.addressHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('wizard.account')" required>
        <el-input v-model="conn.username" placeholder="CORP\svc-hvdirector" autocomplete="off" />
      </el-form-item>
      <el-form-item :label="t('common.password')" required>
        <el-input v-model="conn.password" type="password" show-password autocomplete="new-password" />
      </el-form-item>
      <el-form-item :label="t('clusters.winrmTransport')">
        <el-radio-group v-model="conn.winrm_transport" @change="onTransport">
          <el-radio-button value="http">HTTP (5985)</el-radio-button>
          <el-radio-button value="https">HTTPS (5986)</el-radio-button>
        </el-radio-group>
        <el-input-number v-model="conn.winrm_port" :min="1" :max="65535" controls-position="right" class="port" />
      </el-form-item>
      <el-form-item :label="t('clusters.winrmAuth')">
        <el-select v-model="conn.winrm_auth" style="width: 220px">
          <el-option value="ntlm" label="NTLM" />
          <el-option value="negotiate" label="Negotiate (Kerberos/NTLM)" />
          <el-option value="kerberos" label="Kerberos" />
          <el-option value="credssp" label="CredSSP" />
          <el-option value="basic" label="Basic (HTTPS)" />
        </el-select>
        <el-checkbox v-if="conn.winrm_transport === 'https'" v-model="conn.winrm_cert_validation" class="ml">
          {{ t('clusters.certValidation') }}
        </el-checkbox>
      </el-form-item>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    </el-form>

    <!-- Step 2: discovery result -->
    <div v-if="step === 1 && disc" class="form">
      <el-alert
        :title="disc.kind === 'cluster'
          ? t('wizard.foundCluster', { name: disc.cluster.name, fqdn: disc.cluster.fqdn, n: disc.nodes.length })
          : t('wizard.foundStandalone', { name: disc.seed_host.name })"
        type="success" :closable="false" show-icon class="mb"
      />
      <h4>{{ t('clusters.nodes') }}</h4>
      <div class="form-hint mb">{{ t('wizard.nodesHint') }}</div>
      <el-table :data="nodes" size="small" class="mb">
        <el-table-column prop="name" :label="t('common.name')" width="170" />
        <el-table-column :label="t('common.state')" width="130">
          <template #default="{ row }">
            <el-tag :type="row.reachable ? 'success' : 'danger'" size="small">
              {{ row.reachable ? stateText(row.state || 'Up') : t('wizard.nodeUnreachable') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.address')" width="200">
          <template #default="{ row }">
            <el-select v-if="row.addresses.length > 1" v-model="row.address" size="small" filterable allow-create>
              <el-option v-for="a in row.addresses" :key="a" :value="a" :label="a" />
            </el-select>
            <el-input v-else v-model="row.address" size="small" />
          </template>
        </el-table-column>
        <el-table-column :label="t('clusters.cpu')" width="90">
          <template #default="{ row }">{{ row.info?.logical_cpus ?? '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('clusters.memory')" width="100">
          <template #default="{ row }">{{ row.info ? fmtMb(row.info.memory_total_mb) : '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('clusters.os')" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.info?.os || row.error }}</template>
        </el-table-column>
      </el-table>
      <h4>{{ disc.kind === 'cluster' ? t('wizard.csvs') : t('wizard.volumes') }}</h4>
      <el-table :data="stores" size="small">
        <el-table-column width="70" :label="t('wizard.useAsDatastore')">
          <template #default="{ row }"><el-checkbox v-model="row.use" /></template>
        </el-table-column>
        <el-table-column prop="name" :label="t('common.name')" width="150" />
        <el-table-column :label="t('common.path')" min-width="220">
          <template #default="{ row }"><el-input v-model="row.path" size="small" /></template>
        </el-table-column>
        <el-table-column :label="t('clusters.capacity')" width="150">
          <template #default="{ row }">{{ fmtGb(row.free_gb) }} / {{ fmtGb(row.size_gb) }}</template>
        </el-table-column>
        <el-table-column :label="t('clusters.storageClass')" width="200">
          <template #default="{ row }">
            <el-select v-model="row.storage_class_id" size="small" clearable :placeholder="t('clusters.notAssigned')">
              <el-option v-for="c in classes" :key="c.id" :value="c.id" :label="c.name" />
            </el-select>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Step 3: settings -->
    <el-form v-if="step === 2" label-width="260px" class="form">
      <el-form-item :label="t('wizard.displayName')" required><el-input v-model="settings.name" /></el-form-item>
      <el-form-item :label="t('common.description')"><el-input v-model="settings.description" /></el-form-item>
      <el-form-item :label="t('clusters.vswitch')" required>
        <el-select v-model="settings.vswitch_name" filterable allow-create style="width: 280px">
          <el-option v-for="s in allSwitches" :key="s" :value="s" :label="s" />
        </el-select>
        <div class="form-hint">
          {{ disc.common_switches.length ? t('wizard.commonSwitches') + ': ' + disc.common_switches.join(', ') : t('wizard.noCommonSwitch') }}
        </div>
      </el-form-item>
      <el-form-item v-if="disc.kind === 'cluster'" :label="t('clusters.ha')">
        <el-switch v-model="settings.ha_enabled" />
        <div class="form-hint">{{ t('clusters.haHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('clusters.vmFolder')"><el-input v-model="settings.vm_folder" style="width: 280px" /></el-form-item>
      <el-form-item :label="t('clusters.consoleCreds')">
        <div class="row">
          <el-input v-model="settings.console_username" :placeholder="t('common.username')" style="width: 220px" />
          <el-input v-model="settings.console_password" type="password" show-password :placeholder="t('common.password')" style="width: 200px" />
        </div>
        <div class="form-hint">{{ t('clusters.consoleCredsHint') }}</div>
      </el-form-item>
    </el-form>

    <!-- Step 4: summary -->
    <div v-if="step === 3" class="form">
      <div class="kv">
        <div>{{ t('clusters.kind') }}</div><div>{{ disc.kind === 'cluster' ? t('clusters.kindCluster') : t('clusters.kindStandalone') }}</div>
        <div>{{ t('wizard.displayName') }}</div><div>{{ settings.name }}</div>
        <div v-if="disc.cluster">{{ t('clusters.fqdn') }}</div><div v-if="disc.cluster">{{ disc.cluster.fqdn }} ({{ disc.cluster.address || '—' }})</div>
        <div>{{ t('clusters.nodes') }}</div><div>{{ nodes.map((n) => `${n.name} (${n.address})`).join(', ') }}</div>
        <div>{{ t('clusters.datastores') }}</div>
        <div>{{ stores.filter((s) => s.use).map((s) => `${s.path} → ${className(s.storage_class_id)}`).join('; ') || '—' }}</div>
        <div>{{ t('clusters.vswitch') }}</div><div>{{ settings.vswitch_name }}</div>
        <div v-if="disc.kind === 'cluster'">{{ t('clusters.ha') }}</div><div v-if="disc.kind === 'cluster'">{{ settings.ha_enabled ? t('common.yes') : t('common.no') }}</div>
        <div>WinRM</div><div>{{ conn.winrm_transport }}:{{ conn.winrm_port }} / {{ conn.winrm_auth }} / {{ conn.username }}</div>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mt" />
    </div>

    <template #footer>
      <el-button @click="visible = false">{{ t('common.cancel') }}</el-button>
      <el-button v-if="step > 0" @click="step--">{{ t('common.back') }}</el-button>
      <el-button v-if="step === 0" type="primary" :loading="busy" @click="discover">
        {{ busy ? t('wizard.discovering') : t('wizard.discover') }}
      </el-button>
      <el-button v-else-if="step < 3" type="primary" :disabled="!canNext" @click="step++">{{ t('common.next') }}</el-button>
      <el-button v-else type="primary" :loading="busy" @click="finish">{{ t('common.finish') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api'
import { errorText } from '../i18n'
import { fmtGb, fmtMb, stateText } from '../utils/format'

const emit = defineEmits(['created'])
const { t } = useI18n()
const visible = ref(false)
const step = ref(0)
const busy = ref(false)
const error = ref('')
const disc = ref(null)
const nodes = ref([])
const stores = ref([])
const classes = ref([])
const conn = reactive({})
const settings = reactive({})

function reset() {
  Object.assign(conn, { address: '', username: '', password: '', winrm_transport: 'http', winrm_port: 5985,
    winrm_auth: 'ntlm', winrm_cert_validation: false })
  Object.assign(settings, { name: '', description: '', vswitch_name: '', ha_enabled: true, vm_folder: 'HVDirector',
    console_username: '', console_password: '' })
  step.value = 0
  error.value = ''
  disc.value = null
}

async function open() {
  reset()
  visible.value = true
  try {
    classes.value = await api.get('/api/provider/storage-classes')
  } catch {
    classes.value = []
  }
}

function onTransport(v) {
  conn.winrm_port = v === 'https' ? 5986 : 5985
}

const allSwitches = computed(() => {
  const s = new Set()
  for (const n of nodes.value) for (const sw of n.info?.switches || []) s.add(sw.name)
  return [...s]
})

const canNext = computed(() => {
  if (step.value === 1) return nodes.value.every((n) => n.address)
  if (step.value === 2) return settings.name.trim() && settings.vswitch_name.trim()
  return true
})

function className(id) {
  return classes.value.find((c) => c.id === id)?.name || t('clusters.notAssigned')
}

async function discover() {
  error.value = ''
  busy.value = true
  try {
    const res = await api.post('/api/provider/clusters/discover', { ...conn, address: conn.address.trim() })
    disc.value = res
    nodes.value = res.nodes.map((n) => ({ ...n, addresses: n.addresses.length ? n.addresses : [n.address] }))
    const src = res.kind === 'cluster' ? res.csvs : res.volumes.filter((v) => !v.path.toUpperCase().startsWith('C:'))
    stores.value = src.map((c) => ({
      name: c.name || c.label || c.path, path: res.kind === 'cluster' ? c.path : c.path.replace(/\\$/, '') + '\\Hyper-V',
      size_gb: c.size_gb, free_gb: c.free_gb, use: res.kind === 'cluster', storage_class_id: null,
    }))
    settings.name = res.kind === 'cluster' ? res.cluster.name : res.seed_host.name
    settings.vswitch_name = res.common_switches[0] || ''
    settings.ha_enabled = res.kind === 'cluster'
    step.value = 1
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function finish() {
  error.value = ''
  busy.value = true
  try {
    const d = disc.value
    const body = {
      ...settings,
      kind: d.kind,
      seed_address: conn.address.trim(),
      username: conn.username,
      password: conn.password,
      winrm_transport: conn.winrm_transport,
      winrm_port: conn.winrm_port,
      winrm_auth: conn.winrm_auth,
      winrm_cert_validation: conn.winrm_cert_validation,
      console_username: settings.console_username || null,
      console_password: settings.console_password || null,
      cluster_name: d.cluster?.name || null,
      cluster_fqdn: d.cluster?.fqdn || null,
      cluster_address: d.cluster?.address || null,
      domain: d.domain || d.cluster?.domain || null,
      nodes: nodes.value.map((n) => ({ name: n.name, address: n.address, fqdn: n.info?.fqdn || null })),
      datastores: stores.value.filter((s) => s.use).map((s) => ({
        name: s.name, path: s.path, kind: d.kind === 'cluster' ? 'csv' : 'local',
        storage_class_id: s.storage_class_id, discovered: d.kind === 'cluster',
        capacity_gb: s.size_gb, free_gb: s.free_gb,
      })),
    }
    const res = await api.post('/api/provider/clusters', body)
    visible.value = false
    emit('created', res)
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}

defineExpose({ open })
</script>

<style scoped>
.steps { margin-bottom: 22px; }
.form { min-height: 300px; }
.port { width: 120px; margin-left: 12px; }
.ml { margin-left: 16px; }
.mb { margin-bottom: 12px; }
.mt { margin-top: 12px; }
.row { display: flex; gap: 8px; }
h4 { margin: 14px 0 6px; font-size: 14px; }
</style>
