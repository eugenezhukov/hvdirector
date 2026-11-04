<template>
  <div class="page">
    <div class="page-head">
      <div style="flex: 1">
        <h1>{{ t('networks.title') }}</h1>
        <div class="sub">{{ t('networks.hint') }}</div>
      </div>
      <el-button v-if="canManage" type="primary" :disabled="!freeVlans.length" @click="openNew"><el-icon><IconPlus /></el-icon>{{ t('networks.add') }}</el-button>
    </div>
    <div class="card">
      <div class="toolbar">
        <span class="muted">{{ t('networks.pool') }}:</span>
        <el-tag v-for="v in vlans" :key="v.vlan_id" size="small" :type="v.network ? 'primary' : 'info'" class="vlan">
          {{ v.vlan_id }}<template v-if="v.network"> · {{ v.network }}</template>
        </el-tag>
        <span class="spacer" />
        <span class="muted">{{ t('networks.freeVlans', { n: freeVlans.length }) }}</span>
      </div>
      <el-table v-loading="loading" :data="items" :empty-text="t('common.noData')">
        <el-table-column :label="t('common.name')" min-width="160">
          <template #default="{ row }">{{ row.name }} <el-tag v-if="row.on_router" size="small" type="success">{{ t('nav.edge') }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="vlan_id" label="VLAN" width="90" />
        <el-table-column :label="t('networks.cidr')" width="160"><template #default="{ row }"><span class="mono">{{ row.cidr || '—' }}</span></template></el-table-column>
        <el-table-column :label="t('networks.gateway')" width="140"><template #default="{ row }"><span class="mono">{{ row.gateway || '—' }}</span></template></el-table-column>
        <el-table-column :label="t('networks.dns')" width="160"><template #default="{ row }"><span class="mono">{{ row.dns || '—' }}</span></template></el-table-column>
        <el-table-column prop="nic_count" :label="t('networks.usedBy')" width="130" />
        <el-table-column prop="description" :label="t('common.description')" min-width="160" />
        <el-table-column v-if="canManage" width="100">
          <template #default="{ row }">
            <el-button link @click="openEdit(row)"><el-icon><IconEdit /></el-icon></el-button>
            <el-button link type="danger" :disabled="row.nic_count > 0" @click="remove(row)"><el-icon><IconDelete /></el-icon></el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="dlg" :title="form.id ? t('common.edit') : t('networks.add')" width="520px">
      <el-form label-width="150px">
        <el-form-item :label="t('common.name')" required><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="VLAN">
          <el-select v-model="form.vlan_id" :disabled="!!form.id" clearable :placeholder="t('networks.vlanAuto')" style="width: 260px">
            <el-option v-for="v in freeVlans" :key="v" :value="v" :label="String(v)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.description')"><el-input v-model="form.description" /></el-form-item>
        <div class="form-hint" style="margin: 0 0 8px 150px">{{ t('networks.infoOnly') }}</div>
        <el-form-item :label="t('networks.cidr')"><el-input v-model="form.cidr" placeholder="192.168.10.0/24" /></el-form-item>
        <el-form-item :label="t('networks.gateway')"><el-input v-model="form.gateway" placeholder="192.168.10.1" /></el-form-item>
        <el-form-item :label="t('networks.dns')"><el-input v-model="form.dns" placeholder="192.168.10.1" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { errorText } from '../../i18n'
import { useAuth } from '../../stores/auth'

const { t } = useI18n()
const auth = useAuth()
const items = ref([])
const vlans = ref([])
const loading = ref(false)
const dlg = ref(false)
const form = reactive({})
const canManage = computed(() => auth.can('network.manage'))
const freeVlans = computed(() => vlans.value.filter((v) => !v.network).map((v) => v.vlan_id))

async function load() {
  loading.value = true
  try {
    ;[items.value, vlans.value] = await Promise.all([api.get('/api/tenant/networks'), api.get('/api/tenant/vlans')])
  } finally {
    loading.value = false
  }
}

function openNew() {
  Object.assign(form, { id: null, name: '', vlan_id: null, description: '', cidr: '', gateway: '', dns: '' })
  dlg.value = true
}

function openEdit(r) {
  Object.assign(form, { ...r })
  dlg.value = true
}

async function save() {
  try {
    const body = { name: form.name, description: form.description, cidr: form.cidr || null, gateway: form.gateway || null, dns: form.dns || null }
    if (form.id) await api.patch(`/api/tenant/networks/${form.id}`, body)
    else await api.post('/api/tenant/networks', { ...body, vlan_id: form.vlan_id || null })
    dlg.value = false
    load()
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function remove(r) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: r.name }), t('common.confirmTitle'), { type: 'warning' })
    await api.del(`/api/tenant/networks/${r.id}`)
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>

<style scoped>
.vlan { margin: 0 4px 4px 0; }
</style>
