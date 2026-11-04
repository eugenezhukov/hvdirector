<template>
  <div class="page">
    <div class="page-head">
      <div style="flex: 1"><h1>{{ t('extnet.title') }}</h1><div class="sub">{{ t('extnet.hint') }}</div></div>
      <el-button type="primary" @click="open()"><el-icon><IconPlus /></el-icon>{{ t('extnet.add') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items" :empty-text="t('common.noData')">
        <el-table-column prop="name" :label="t('common.name')" min-width="160" />
        <el-table-column :label="t('extnet.purpose')" width="190">
          <template #default="{ row }"><el-tag size="small" :type="row.purpose === 'wan' ? 'primary' : 'warning'">{{ t('extnet.' + row.purpose) }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="vlan_id" label="VLAN" width="80" />
        <el-table-column :label="t('networks.cidr')" width="160"><template #default="{ row }"><span class="mono">{{ row.cidr }}</span></template></el-table-column>
        <el-table-column :label="t('networks.gateway')" width="140"><template #default="{ row }"><span class="mono">{{ row.gateway || '—' }}</span></template></el-table-column>
        <el-table-column :label="t('networks.dns')" width="150"><template #default="{ row }"><span class="mono">{{ row.dns || '—' }}</span></template></el-table-column>
        <el-table-column :label="t('extnet.vswitch')" min-width="140"><template #default="{ row }">{{ row.vswitch_name || '—' }}</template></el-table-column>
        <el-table-column :label="t('extnet.allocated')" width="130"><template #default="{ row }">{{ row.allocated }} / {{ row.usable }}</template></el-table-column>
        <el-table-column width="100" align="right">
          <template #default="{ row }">
            <el-button link @click="open(row)"><el-icon><IconEdit /></el-icon></el-button>
            <el-button link type="danger" :disabled="row.allocated > 0" @click="remove(row)"><el-icon><IconDelete /></el-icon></el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="dlg" :title="form.id ? t('common.edit') : t('extnet.add')" width="600px">
      <el-form label-width="200px">
        <el-form-item :label="t('common.name')" required><el-input v-model="form.name" /></el-form-item>
        <el-form-item :label="t('extnet.purpose')">
          <el-radio-group v-model="form.purpose" :disabled="!!form.id">
            <el-radio-button value="wan">{{ t('extnet.wan') }}</el-radio-button>
            <el-radio-button value="mgmt">{{ t('extnet.mgmt') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="VLAN" required><el-input-number v-model="form.vlan_id" :min="1" :max="4094" :disabled="!!form.id" /></el-form-item>
        <el-form-item :label="t('networks.cidr')" required><el-input v-model="form.cidr" placeholder="203.0.113.0/24" :disabled="!!form.id" /></el-form-item>
        <el-form-item :label="t('networks.gateway')"><el-input v-model="form.gateway" placeholder="203.0.113.1" /></el-form-item>
        <el-form-item :label="t('networks.dns')"><el-input v-model="form.dns" placeholder="203.0.113.53, 8.8.8.8" /></el-form-item>
        <el-form-item :label="t('extnet.vswitch')">
          <el-input v-model="form.vswitch_name" />
          <div class="form-hint">{{ t('extnet.vswitchHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('common.description')"><el-input v-model="form.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { errorText } from '../../i18n'

const { t } = useI18n()
const items = ref([])
const loading = ref(false)
const dlg = ref(false)
const form = reactive({})

async function load() {
  loading.value = true
  try {
    items.value = await api.get('/api/provider/external-networks')
  } finally {
    loading.value = false
  }
}

function open(n = null) {
  Object.assign(form, n ? { ...n, gateway: n.gateway || '' } : { id: null, name: '', purpose: 'wan', vlan_id: 100, cidr: '',
    gateway: '', dns: '', vswitch_name: '', description: '' })
  dlg.value = true
}

async function save() {
  try {
    if (form.id) {
      await api.patch(`/api/provider/external-networks/${form.id}`, { name: form.name, gateway: form.gateway || null,
        dns: form.dns, vswitch_name: form.vswitch_name, description: form.description })
    } else {
      await api.post('/api/provider/external-networks', { ...form, id: undefined, gateway: form.gateway || null })
    }
    dlg.value = false
    load()
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function remove(n) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: n.name }), t('common.confirmTitle'), { type: 'warning' })
    await api.del(`/api/provider/external-networks/${n.id}`)
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>
