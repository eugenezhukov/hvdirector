<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('routers.title') }}</h1>
      <el-button @click="load"><el-icon><IconRefresh /></el-icon>{{ t('common.refresh') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items" :empty-text="t('common.noData')">
        <el-table-column :label="t('common.organization')" width="110"><template #default="{ row }"><span class="mono">{{ row.tenant }}</span></template></el-table-column>
        <el-table-column prop="name" :label="t('common.name')" min-width="100" />
        <el-table-column :label="t('common.status')" width="140">
          <template #default="{ row }">
            <el-tag size="small" :type="{ ready: 'success', deploying: 'primary', error: 'danger', deleting: 'warning' }[row.status]">{{ t('edge.status.' + row.status) }}</el-tag>
            <el-tooltip v-if="row.status_message || row.last_error" :content="row.status_message || row.last_error">
              <el-icon color="#d9822b" class="ml"><IconWarningFilled /></el-icon>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column :label="t('routers.mgmtIp')" width="150"><template #default="{ row }"><span class="mono">{{ row.mgmt_ip }}</span><div class="muted small">VLAN {{ row.mgmt_vlan }}</div></template></el-table-column>
        <el-table-column :label="t('edge.wanIps')" min-width="150" show-overflow-tooltip>
          <template #default="{ row }"><span class="mono">{{ row.wan ? row.wan.ips.map((i) => i.address).join(', ') : '—' }}</span></template>
        </el-table-column>
        <el-table-column :label="t('edge.networks')" min-width="130" show-overflow-tooltip><template #default="{ row }">{{ row.networks.map((n) => `${n.name} (${n.vlan_id})`).join(', ') || '—' }}</template></el-table-column>
        <el-table-column :label="t('common.host')" width="130"><template #default="{ row }">{{ row.vm?.host || '—' }}</template></el-table-column>
        <el-table-column :label="t('routers.version')" width="100"><template #default="{ row }">{{ row.applied_version }} / {{ row.config_version }}</template></el-table-column>
        <el-table-column width="150" align="right" fixed="right">
          <template #default="{ row }">
            <el-tooltip :content="t('vms.console')"><el-button link :disabled="row.vm?.power_state !== 'Running'" @click="consoleDlg.open({ id: row.vm.id, name: row.vm.hv_name })"><el-icon><IconMonitor /></el-icon></el-button></el-tooltip>
            <el-tooltip :content="t('routers.showPassword')"><el-button link @click="secret(row)"><el-icon><IconKey /></el-icon></el-button></el-tooltip>
            <el-tooltip :content="t('edge.reapply')"><el-button link :disabled="row.status === 'deploying' || row.status === 'deleting'" @click="apply(row)"><el-icon><IconRefresh /></el-icon></el-button></el-tooltip>
            <el-button link type="danger" :disabled="row.status === 'deleting'" @click="remove(row)"><el-icon><IconDelete /></el-icon></el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <ConsoleDialog ref="consoleDlg" provider />
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { errorText } from '../../i18n'
import { useTasks } from '../../stores/tasks'
import ConsoleDialog from '../../components/ConsoleDialog.vue'

const { t } = useI18n()
const tasks = useTasks()
const items = ref([])
const loading = ref(false)
const consoleDlg = ref(null)
let timer = null

async function load() {
  loading.value = !items.value.length
  try {
    items.value = await api.get('/api/provider/routers')
  } finally {
    loading.value = false
  }
}

async function secret(r) {
  try {
    const s = await api.get(`/api/provider/routers/${r.id}/secret`)
    ElMessageBox.alert(`root: ${s.root_password}`, `${t('routers.rootPassword')} — ${r.tenant}`, { confirmButtonText: t('common.close') })
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function apply(r) {
  try {
    tasks.track(await api.post(`/api/provider/routers/${r.id}/apply`)).then(load)
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function remove(r) {
  try {
    await ElMessageBox.confirm(t('edge.deleteConfirm', { name: `${r.tenant}/${r.name}` }), t('common.confirmTitle'), { type: 'error', confirmButtonText: t('common.yesDelete') })
  } catch {
    return
  }
  try {
    tasks.track(await api.del(`/api/provider/routers/${r.id}`)).then(load)
    load()
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 8000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.ml { margin-left: 6px; vertical-align: middle; }
.small { font-size: 12px; }
</style>
