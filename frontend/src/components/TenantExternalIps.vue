<template>
  <div>
    <div class="toolbar">
      <span class="muted">{{ t('extnet.mgmtNote') }}</span>
      <span class="spacer" />
      <el-button size="small" type="primary" @click="openAlloc"><el-icon><IconPlus /></el-icon>{{ t('extnet.allocate') }}</el-button>
    </div>
    <el-table :data="items" size="small" :empty-text="t('common.noData')">
      <el-table-column :label="t('extnet.purpose')" width="190">
        <template #default="{ row }"><el-tag size="small" :type="row.purpose === 'wan' ? 'primary' : 'warning'">{{ t('extnet.' + row.purpose) }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.address')" width="160"><template #default="{ row }"><span class="mono">{{ row.address }}</span></template></el-table-column>
      <el-table-column prop="network" :label="t('extnet.title')" min-width="140" />
      <el-table-column prop="vlan_id" label="VLAN" width="80" />
      <el-table-column :label="t('extnet.usedBy')" min-width="150"><template #default="{ row }">{{ row.used_by.join(', ') || '—' }}</template></el-table-column>
      <el-table-column width="120" align="right">
        <template #default="{ row }">
          <el-button link type="danger" :disabled="row.used_by.length > 0" @click="release(row)">{{ t('extnet.release') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="router" class="router-line muted">
      {{ t('routers.title') }}: <b>{{ router.name }}</b> · {{ t('edge.status.' + router.status) }} · {{ t('routers.version') }} {{ router.applied_version }}/{{ router.config_version }}
    </div>
    <el-dialog v-model="dlg" :title="t('extnet.allocate')" width="560px" append-to-body>
      <el-form label-width="170px">
        <el-form-item :label="t('extnet.title')">
          <el-select v-model="form.network_id" style="width: 100%">
            <el-option v-for="n in nets" :key="n.id" :value="n.id" :label="`${n.name} · ${t('extnet.' + n.purpose)} · VLAN ${n.vlan_id} · ${n.cidr}`" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('extnet.addresses')">
          <el-input v-model="form.addresses" :disabled="!!form.count" />
          <div class="form-hint">{{ t('extnet.addressesHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('extnet.count')"><el-input-number v-model="form.count" :min="0" :max="256" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!form.network_id" @click="allocate">{{ t('extnet.allocate') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { errorText } from '../i18n'

const props = defineProps({ tenantId: { type: String, required: true } })
const { t } = useI18n()
const items = ref([])
const router = ref(null)
const nets = ref([])
const dlg = ref(false)
const form = reactive({ network_id: null, addresses: '', count: 0 })

async function load() {
  const res = await api.get(`/api/provider/tenants/${props.tenantId}/external-ips`)
  items.value = res.items
  router.value = res.router
}

async function openAlloc() {
  nets.value = await api.get('/api/provider/external-networks')
  Object.assign(form, { network_id: nets.value[0]?.id || null, addresses: '', count: 0 })
  dlg.value = true
}

async function allocate() {
  try {
    await api.post(`/api/provider/tenants/${props.tenantId}/external-ips`, { network_id: form.network_id,
      addresses: form.count ? '' : form.addresses, count: form.count || null })
    dlg.value = false
    load()
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function release(ip) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: ip.address }), t('common.confirmTitle'), { type: 'warning' })
    await api.del(`/api/provider/external-ips/${ip.id}`)
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>

<style scoped>
.router-line { margin-top: 12px; font-size: 13px; }
</style>
