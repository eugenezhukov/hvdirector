<template>
  <div>
    <!-- CPU and memory -->
    <h4>
      {{ t('vms.compute') }}: {{ vm.vcpu }} vCPU · {{ fmtMb(vm.memory_mb) }}
      <el-button v-if="canEdit" size="small" class="ml" :disabled="!ready" @click="openCompute">
        <el-icon><IconEdit /></el-icon>{{ t('common.edit') }}
      </el-button>
    </h4>

    <!-- disks -->
    <h4>
      {{ t('vms.disks') }}
      <el-button v-if="canEdit" size="small" class="ml" :disabled="!ready" @click="openAddDisk"><el-icon><IconPlus /></el-icon>{{ t('vms.addDisk') }}</el-button>
    </h4>
    <el-alert v-if="canEdit && vm.snapshots.length" :title="t('vms.snapshotsBlockDisks')" type="info" show-icon :closable="false" class="mb" />
    <el-table :data="vm.disks" size="small">
      <el-table-column label="#" width="60">
        <template #default="{ row }">{{ row.position }} <el-tag v-if="row.position === 0" size="small" type="info">{{ t('vms.systemDisk') }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.size')" width="110"><template #default="{ row }">{{ fmtGb(row.size_gb) }}</template></el-table-column>
      <el-table-column prop="storage_class" :label="t('vms.storageClass')" min-width="140" />
      <el-table-column prop="datastore" :label="t('vms.datastore')" min-width="120" />
      <el-table-column v-if="canEdit" width="190" align="right">
        <template #default="{ row }">
          <el-tooltip :content="diskBlock(row)" :disabled="!diskBlock(row)">
            <span><el-button size="small" :disabled="!ready || !!diskBlock(row)" @click="openExtend(row)"><el-icon><IconZoomIn /></el-icon>{{ t('vms.extend') }}</el-button></span>
          </el-tooltip>
          <el-button v-if="row.position !== 0" size="small" type="danger" plain :disabled="!ready || vm.snapshots.length > 0"
                     :title="t('vms.removeDisk')" @click="removeDisk(row)"><el-icon><IconDelete /></el-icon></el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- network adapters -->
    <h4>
      {{ t('vms.nics') }}
      <el-tooltip v-if="canEdit" :content="t('vms.gen1NicOff')" :disabled="!gen1Running">
        <span class="ml"><el-button size="small" :disabled="!ready || gen1Running || vm.nics.length >= 8" @click="openAddNic"><el-icon><IconPlus /></el-icon>{{ t('vms.addNic') }}</el-button></span>
      </el-tooltip>
    </h4>
    <el-table :data="vm.nics" size="small">
      <el-table-column prop="name" :label="t('common.name')" width="90" />
      <el-table-column :label="t('vms.network')" min-width="240">
        <template #default="{ row }">
          <el-select v-if="canEdit" :model-value="row.network_id" clearable size="small" :placeholder="t('vms.disconnected')"
                     :disabled="!ready" style="width: 260px" @change="(v) => changeNic(row, v)">
            <el-option v-for="n in networks" :key="n.id" :value="n.id" :label="`${n.name} (VLAN ${n.vlan_id})`" />
          </el-select>
          <span v-else>{{ row.network ? `${row.network} (VLAN ${row.vlan_id})` : t('vms.disconnected') }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('vms.mac')" width="170"><template #default="{ row }"><span class="mono">{{ row.mac || '—' }}</span></template></el-table-column>
      <el-table-column v-if="canEdit" width="60" align="right">
        <template #default="{ row }">
          <el-tooltip :content="t('vms.gen1NicOff')" :disabled="!gen1Running">
            <span><el-button link type="danger" :disabled="!ready || gen1Running" @click="removeNic(row)"><el-icon><IconDelete /></el-icon></el-button></span>
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <!-- dialogs -->
    <el-dialog v-model="computeDlg" :title="t('vms.compute')" width="520px" append-to-body>
      <el-form label-width="150px">
        <el-form-item label="vCPU">
          <el-input-number v-model="compute.vcpu" :min="1" :max="64" :disabled="vm.power_state !== 'Off'" />
          <div v-if="vm.power_state !== 'Off'" class="form-hint">{{ t('vms.cpuOffOnly') }}</div>
        </el-form-item>
        <el-form-item :label="t('vmWizard.memoryGb')">
          <el-input-number v-model="compute.memory_gb" :min="0.5" :step="1" :precision="1"
                           :disabled="!['Off', 'Running'].includes(vm.power_state)" />
          <div v-if="vm.power_state === 'Running'" class="form-hint">{{ t('vms.memoryHot') }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="computeDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveCompute">{{ t('common.apply') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="extendDlg" :title="t('vms.extendTitle', { n: extendForm.position })" width="480px" append-to-body>
      <el-form label-width="170px">
        <el-form-item :label="t('vms.currentSize')">{{ fmtGb(extendForm.current) }}</el-form-item>
        <el-form-item :label="t('vms.newSize')">
          <el-input-number v-model="extendForm.size_gb" :min="extendForm.current + 1" :max="65536" />
        </el-form-item>
      </el-form>
      <div class="form-hint">{{ t('vms.extendHint') }}</div>
      <template #footer>
        <el-button @click="extendDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!(extendForm.size_gb > extendForm.current)" @click="saveExtend">{{ t('vms.extend') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="diskDlg" :title="t('vms.addDisk')" width="480px" append-to-body>
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

    <el-dialog v-model="nicDlg" :title="t('vms.addNicTitle')" width="440px" append-to-body>
      <el-form label-width="110px">
        <el-form-item :label="t('vms.network')">
          <el-select v-model="newNic.network_id" clearable :placeholder="t('vms.disconnected')" style="width: 100%">
            <el-option v-for="n in networks" :key="n.id" :value="n.id" :label="`${n.name} (VLAN ${n.vlan_id})`" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="nicDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveNic">{{ t('common.add') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
// Hardware of a VM: vCPU / memory, disks (add, extend, remove) and network adapters (add, change network, remove).
// Used by the tenant VM page and by the provider VM page; `reconfigure(body)` posts to the matching API.
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { fmtGb, fmtMb } from '../utils/format'

const props = defineProps({
  vm: { type: Object, required: true },
  ready: { type: Boolean, default: false },
  canEdit: { type: Boolean, default: false },
  networks: { type: Array, default: () => [] },
  loadClasses: { type: Function, required: true },
  reconfigure: { type: Function, required: true },
})
const { t } = useI18n()

const gen1Running = computed(() => props.vm.generation === 1 && props.vm.power_state !== 'Off')
function diskBlock(d) {
  if (props.vm.snapshots.length) return t('vms.snapshotsBlockDisks')
  if (props.vm.generation === 1 && d.position === 0 && props.vm.power_state !== 'Off') return t('vms.gen1IdeOff')
  return ''
}

// ---- CPU / memory
const computeDlg = ref(false)
const compute = reactive({ vcpu: 1, memory_gb: 1 })
function openCompute() {
  compute.vcpu = props.vm.vcpu
  compute.memory_gb = props.vm.memory_mb / 1024
  computeDlg.value = true
}
async function saveCompute() {
  const body = {}
  if (compute.vcpu !== props.vm.vcpu) body.vcpu = compute.vcpu
  const mb = Math.round(compute.memory_gb * 1024)
  if (mb !== props.vm.memory_mb) body.memory_mb = mb
  computeDlg.value = false
  if (Object.keys(body).length) await props.reconfigure(body)
}

// ---- disks
const extendDlg = ref(false)
const extendForm = reactive({ disk_id: null, position: 0, current: 0, size_gb: 0 })
function openExtend(d) {
  Object.assign(extendForm, { disk_id: d.id, position: d.position, current: d.size_gb, size_gb: d.size_gb + 10 })
  extendDlg.value = true
}
async function saveExtend() {
  extendDlg.value = false
  await props.reconfigure({ disk_ops: [{ op: 'resize', disk_id: extendForm.disk_id, size_gb: extendForm.size_gb }] })
}
async function removeDisk(d) {
  try {
    await ElMessageBox.confirm(t('vms.removeDiskConfirm', { n: d.position, size: fmtGb(d.size_gb) }), t('vms.removeDisk'),
      { type: 'error', confirmButtonText: t('common.yesDelete'), cancelButtonText: t('common.cancel') })
  } catch {
    return
  }
  await props.reconfigure({ disk_ops: [{ op: 'remove', disk_id: d.id }] })
}
const diskDlg = ref(false)
const classes = ref([])
const newDisk = reactive({ size_gb: 50, storage_class_id: null })
async function openAddDisk() {
  classes.value = await props.loadClasses()
  newDisk.storage_class_id = classes.value.find((c) => c.available)?.id || null
  diskDlg.value = true
}
async function saveDisk() {
  diskDlg.value = false
  await props.reconfigure({ add_disks: [{ ...newDisk }] })
}

// ---- network adapters
const nicDlg = ref(false)
const newNic = reactive({ network_id: null })
function openAddNic() {
  newNic.network_id = props.networks[0]?.id || null
  nicDlg.value = true
}
async function saveNic() {
  nicDlg.value = false
  await props.reconfigure({ nics: [{ op: 'add', network_id: newNic.network_id || null }] })
}
const changeNic = (nic, networkId) => props.reconfigure({ nics: [{ op: 'set', nic_id: nic.id, network_id: networkId || null }] })
async function removeNic(nic) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: nic.name }), t('common.confirmTitle'), { type: 'warning' })
  } catch {
    return
  }
  await props.reconfigure({ nics: [{ op: 'remove', nic_id: nic.id }] })
}
</script>

<style scoped>
.ml { margin-left: 10px; }
.mb { margin-bottom: 8px; }
h4 { margin: 16px 0 8px; font-size: 14px; display: flex; align-items: center; }
</style>
