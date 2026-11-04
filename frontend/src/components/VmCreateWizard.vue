<template>
  <el-dialog v-model="visible" :title="t('vmWizard.title')" width="860px" top="5vh" :close-on-click-modal="false" destroy-on-close>
    <el-steps :active="step" finish-status="success" align-center class="steps">
      <el-step v-for="i in 6" :key="i" :title="t('vmWizard.step' + i)" />
    </el-steps>

    <el-form label-width="200px" class="body" @submit.prevent>
      <template v-if="step === 0">
        <el-form-item :label="t('common.name')" required>
          <el-input v-model="f.name" maxlength="63" style="width: 300px" />
          <div class="form-hint">{{ t('vmWizard.nameHint') }} · Hyper-V: <span class="mono">{{ slug }}_{{ f.name || '…' }}</span></div>
        </el-form-item>
        <el-form-item :label="t('common.description')"><el-input v-model="f.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item :label="t('vms.generation')">
          <el-radio-group v-model="f.generation">
            <el-radio-button :value="2">{{ t('vms.gen2') }}</el-radio-button>
            <el-radio-button :value="1">{{ t('vms.gen1') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="f.generation === 2" :label="t('vms.secureBoot')">
          <el-select v-model="f.secure_boot" style="width: 300px">
            <el-option v-for="o in ['windows', 'linux', 'off']" :key="o" :value="o" :label="t('vms.secureBootOpts.' + o)" />
          </el-select>
        </el-form-item>
      </template>

      <template v-if="step === 1">
        <el-form-item label="vCPU">
          <el-input-number v-model="f.vcpu" :min="1" :max="64" />
          <span class="muted ml">{{ quotaHint('vcpu', f.vcpu) }}</span>
        </el-form-item>
        <el-form-item :label="t('vmWizard.memoryGb')">
          <el-input-number v-model="f.memory_gb" :min="0.5" :max="1024" :step="1" :precision="1" />
          <span class="muted ml">{{ quotaHint('memory_mb', f.memory_gb * 1024, fmtMb) }}</span>
        </el-form-item>
        <el-alert :title="t('vmWizard.placementHint')" type="info" :closable="false" show-icon />
      </template>

      <template v-if="step === 2">
        <el-alert v-if="!classes.length" :title="t('vmWizard.noClasses')" type="warning" :closable="false" show-icon />
        <div v-for="(d, i) in f.disks" :key="i" class="disk-row">
          <span class="lbl">{{ i === 0 ? t('vmWizard.boot') : `${t('vms.disk')} ${i}` }}</span>
          <el-input-number v-model="d.size_gb" :min="1" :max="65536" style="width: 150px" />
          <span class="muted">{{ t('common.gb') }}</span>
          <el-select v-model="d.storage_class_id" style="width: 280px">
            <el-option v-for="c in classes" :key="c.id" :value="c.id" :disabled="!c.available"
                       :label="`${c.name} — ${c.limit_gb === null ? t('quota.unlimited') : t('vmWizard.available', { n: fmtGb(c.limit_gb - c.used_gb) })}`" />
          </el-select>
          <el-button v-if="i > 0" link type="danger" @click="f.disks.splice(i, 1)"><el-icon><IconDelete /></el-icon></el-button>
        </div>
        <el-button size="small" :disabled="!classes.length" @click="f.disks.push({ size_gb: 50, storage_class_id: classes[0]?.id })">
          <el-icon><IconPlus /></el-icon>{{ t('vms.addDisk') }}
        </el-button>
        <div v-for="(sum, id) in storageSum" :key="id" class="muted small">{{ className(id) }}: {{ quotaHint('storage', sum, fmtGb, id) }}</div>
      </template>

      <template v-if="step === 3">
        <el-alert v-if="!networks.length" :title="t('vmWizard.noNetworks')" type="warning" :closable="false" show-icon class="mb" />
        <div v-for="(n, i) in f.nics" :key="i" class="disk-row">
          <span class="lbl">nic{{ i }}</span>
          <el-select v-model="n.network_id" clearable :placeholder="t('vms.disconnected')" style="width: 320px">
            <el-option v-for="net in networks" :key="net.id" :value="net.id" :label="`${net.name} (VLAN ${net.vlan_id})`" />
          </el-select>
          <el-button link type="danger" @click="f.nics.splice(i, 1)"><el-icon><IconDelete /></el-icon></el-button>
        </div>
        <el-button size="small" :disabled="f.nics.length >= 8" @click="f.nics.push({ network_id: networks[0]?.id || null })">
          <el-icon><IconPlus /></el-icon>{{ t('vms.addNic') }}
        </el-button>
      </template>

      <template v-if="step === 4">
        <el-form-item :label="t('vmWizard.iso')">
          <el-select v-model="f.iso_id" clearable :placeholder="t('vmWizard.noIso')" style="width: 420px" filterable>
            <el-option v-for="iso in isos" :key="iso.id" :value="iso.id" :label="iso.name">
              <span>{{ iso.name }}</span>
              <el-tag v-if="iso.public" size="small" type="success" class="ml">{{ t('common.public') }}</el-tag>
              <span class="muted ml">{{ fmtBytes(iso.size_bytes) }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item :label="t('vms.powerOnAfter')"><el-switch v-model="f.power_on" /></el-form-item>
      </template>

      <template v-if="step === 5">
        <div class="kv">
          <div>{{ t('common.name') }}</div><div><b>{{ f.name }}</b> <span class="muted mono">({{ slug }}_{{ f.name }})</span></div>
          <div>{{ t('vms.generation') }}</div><div>{{ f.generation === 2 ? t('vms.gen2') : t('vms.gen1') }}<template v-if="f.generation === 2"> · Secure Boot: {{ t('vms.secureBootOpts.' + f.secure_boot) }}</template></div>
          <div>vCPU / {{ t('vms.memory') }}</div><div>{{ f.vcpu }} / {{ fmtMb(f.memory_gb * 1024) }}</div>
          <div>{{ t('vms.disks') }}</div><div>{{ f.disks.map((d) => `${d.size_gb} ${t('common.gb')} (${className(d.storage_class_id)})`).join(', ') }}</div>
          <div>{{ t('vms.nics') }}</div><div>{{ f.nics.map((n) => netName(n.network_id)).join(', ') || '—' }}</div>
          <div>ISO</div><div>{{ isos.find((i) => i.id === f.iso_id)?.name || '—' }}</div>
          <div>{{ t('vms.powerOnAfter') }}</div><div>{{ f.power_on ? t('common.yes') : t('common.no') }}</div>
        </div>
      </template>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mt" />
    </el-form>

    <template #footer>
      <el-button @click="visible = false">{{ t('common.cancel') }}</el-button>
      <el-button v-if="step > 0" @click="step--">{{ t('common.back') }}</el-button>
      <el-button v-if="step < 5" type="primary" :disabled="!stepValid" @click="step++">{{ t('common.next') }}</el-button>
      <el-button v-else type="primary" :loading="busy" @click="create">{{ t('common.create') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api'
import { errorText } from '../i18n'
import { useAuth } from '../stores/auth'
import { fmtBytes, fmtGb, fmtMb } from '../utils/format'

const emit = defineEmits(['created'])
const { t } = useI18n()
const auth = useAuth()
const visible = ref(false)
const step = ref(0)
const busy = ref(false)
const error = ref('')
const classes = ref([])
const networks = ref([])
const isos = ref([])
const quotas = ref(null)
const f = reactive({})
const slug = computed(() => auth.tenant?.slug)
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/

const storageSum = computed(() => {
  const s = {}
  for (const d of f.disks || []) if (d.storage_class_id) s[d.storage_class_id] = (s[d.storage_class_id] || 0) + d.size_gb
  return s
})

const stepValid = computed(() => {
  if (step.value === 0) return NAME_RE.test(f.name || '')
  if (step.value === 2) return f.disks.length > 0 && f.disks.every((d) => d.size_gb > 0 && d.storage_class_id)
  return true
})

function className(id) {
  return classes.value.find((c) => c.id === id)?.name || '—'
}
function netName(id) {
  const n = networks.value.find((x) => x.id === id)
  return n ? `${n.name} (VLAN ${n.vlan_id})` : t('vms.disconnected')
}

function quotaHint(kind, requested, fmt = (v) => v, scId = null) {
  if (!quotas.value) return ''
  let used
  let limit
  if (kind === 'storage') {
    const c = classes.value.find((x) => x.id === scId)
    if (!c) return ''
    used = c.used_gb
    limit = c.limit_gb
  } else {
    used = quotas.value[kind].used
    limit = quotas.value[kind].limit
  }
  if (limit === null) return `${t('vmWizard.afterQuota')}: ${fmt(used + requested)} · ${t('quota.unlimited')}`
  return `${t('vmWizard.afterQuota')}: ${fmt(used + requested)} ${t('common.of')} ${fmt(limit)}`
}

async function open() {
  error.value = ''
  step.value = 0
  const [cls, nets, img, summary] = await Promise.all([
    api.get('/api/tenant/storage-classes'),
    api.get('/api/tenant/networks').catch(() => []),
    api.get('/api/tenant/isos').catch(() => []),
    api.get('/api/tenant/summary'),
  ])
  classes.value = cls
  networks.value = nets
  isos.value = img.filter((i) => i.status === 'ready')
  quotas.value = summary.quotas || null
  const firstClass = cls.find((c) => c.available)
  Object.assign(f, {
    name: '', description: '', generation: 2, secure_boot: 'windows', vcpu: 2, memory_gb: 4,
    disks: [{ size_gb: 60, storage_class_id: firstClass?.id || null }],
    nics: nets.length ? [{ network_id: nets[0].id }] : [], iso_id: null, power_on: false,
  })
  visible.value = true
}

async function create() {
  busy.value = true
  error.value = ''
  try {
    const res = await api.post('/api/tenant/vms', {
      name: f.name.trim(), description: f.description, generation: f.generation,
      secure_boot: f.generation === 2 ? f.secure_boot : 'off', vcpu: f.vcpu,
      memory_mb: Math.round(f.memory_gb * 1024), disks: f.disks, nics: f.nics, iso_id: f.iso_id || null,
      power_on: f.power_on,
    })
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
.body { min-height: 260px; }
.disk-row { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.lbl { width: 130px; text-align: right; color: #3c4b57; font-size: 14px; }
.ml { margin-left: 10px; }
.mb { margin-bottom: 12px; }
.mt { margin-top: 12px; }
.small { font-size: 12px; margin-top: 6px; }
</style>
