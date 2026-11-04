<template>
  <div v-if="tn" class="page">
    <div class="page-head">
      <div style="flex: 1">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item :to="'/provider/tenants'">{{ t('nav.tenants') }}</el-breadcrumb-item>
          <el-breadcrumb-item>{{ tn.slug }}</el-breadcrumb-item>
        </el-breadcrumb>
        <h1 style="margin-top: 8px">{{ tn.name }} <el-tag size="small" :type="tn.enabled ? 'success' : 'info'">{{ tn.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag></h1>
        <div class="sub">
          {{ t('tenants.loginUrl') }}: <a :href="loginUrl" target="_blank" class="mono">{{ loginUrl }}</a>
          · {{ t('tenants.cluster') }}: {{ tn.cluster_name }}
        </div>
      </div>
      <el-button @click="dlg.open(tn)"><el-icon><IconEdit /></el-icon>{{ t('common.edit') }}</el-button>
      <el-tooltip :content="t('tenants.deleteHint')">
        <el-button type="danger" plain @click="remove"><el-icon><IconDelete /></el-icon>{{ t('common.delete') }}</el-button>
      </el-tooltip>
    </div>
    <div class="grid-2">
      <div class="card">
        <h3>{{ t('tenants.usage') }}</h3>
        <QuotaBar :label="t('quota.vcpu')" :used="tn.usage.vcpu.used" :limit="tn.usage.vcpu.limit" />
        <QuotaBar :label="t('quota.memory')" :used="tn.usage.memory_mb.used" :limit="tn.usage.memory_mb.limit" :format="fmtMb" />
        <QuotaBar :label="t('quota.vms')" :used="tn.usage.vms.used" :limit="tn.usage.vms.limit" />
        <QuotaBar :label="t('quota.snapshots')" :used="tn.usage.snapshots.used" :limit="tn.usage.snapshots.limit" />
        <QuotaBar :label="t('quota.iso_gb')" :used="tn.usage.iso_gb.used" :limit="tn.usage.iso_gb.limit" :format="fmtGb" />
        <QuotaBar v-for="s in tn.usage.storage" :key="s.storage_class_id" :label="`${t('quota.storage')}: ${s.name}`" :used="s.used_gb" :limit="s.limit_gb" :format="fmtGb" />
      </div>
      <div class="card">
        <h3>{{ t('tenants.networks') }} · <span class="muted">{{ t('tenants.vlans') }}: <span class="mono">{{ tn.vlans || '—' }}</span></span></h3>
        <el-table :data="tn.networks" size="small" :empty-text="t('common.noData')">
          <el-table-column prop="name" :label="t('common.name')" />
          <el-table-column prop="vlan_id" label="VLAN" width="90" />
        </el-table>
      </div>
    </div>
    <el-tabs class="card" model-value="vms">
      <el-tab-pane :label="`${t('tenants.vms_')} (${tn.vms.length})`" name="vms">
        <el-table :data="tn.vms" size="small" :empty-text="t('common.noData')">
          <el-table-column prop="name" :label="t('common.name')" min-width="140" />
          <el-table-column prop="hv_name" label="Hyper-V" min-width="160" />
          <el-table-column :label="t('common.state')" width="150"><template #default="{ row }"><VmState :vm="row" /></template></el-table-column>
          <el-table-column prop="host" :label="t('common.host')" width="150" />
          <el-table-column prop="vcpu" label="vCPU" width="70" />
          <el-table-column :label="t('vms.memory')" width="100"><template #default="{ row }">{{ fmtMb(row.memory_mb) }}</template></el-table-column>
          <el-table-column :label="t('vms.disk')" width="100"><template #default="{ row }">{{ fmtGb(row.disk_gb) }}</template></el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="`${t('tenants.users')} (${users.length})`" name="users">
        <div class="toolbar"><span class="spacer" /><el-button size="small" @click="udlg.open()"><el-icon><IconPlus /></el-icon>{{ t('users.add') }}</el-button></div>
        <el-table :data="users" size="small">
          <el-table-column prop="username" :label="t('common.username')" width="160" />
          <el-table-column prop="full_name" :label="t('common.fullName')" min-width="160" />
          <el-table-column :label="t('users.roles')" min-width="260">
            <template #default="{ row }">
              <el-tag v-for="r in row.roles" :key="r.id" size="small" class="role">
                {{ t('users.roleNames.' + r.role) }} · {{ r.vm_name ? t('users.scopeVm', { name: r.vm_name }) : t('users.scopeTenant') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.status')" width="120">
            <template #default="{ row }">
              <el-tag v-if="row.locked" type="danger" size="small">{{ t('common.locked') }}</el-tag>
              <el-tag v-else :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? t('common.active') : t('common.disabled') }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column width="100">
            <template #default="{ row }">
              <el-button link @click="udlg.open(row)"><el-icon><IconEdit /></el-icon></el-button>
              <el-button link type="danger" @click="removeUser(row)"><el-icon><IconDelete /></el-icon></el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
    <TenantDialog ref="dlg" @saved="load" />
    <UserDialog ref="udlg" :base-url="`/api/provider/tenants/${tn.id}/users`" :vms="tn.vms" @saved="loadUsers" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { errorText } from '../../i18n'
import QuotaBar from '../../components/QuotaBar.vue'
import TenantDialog from '../../components/TenantDialog.vue'
import UserDialog from '../../components/UserDialog.vue'
import VmState from '../../components/VmState.vue'
import { fmtGb, fmtMb } from '../../utils/format'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const tn = ref(null)
const users = ref([])
const dlg = ref(null)
const udlg = ref(null)
const loginUrl = computed(() => `${window.location.origin}/tenant/${tn.value.slug}/login`)

async function load() {
  tn.value = await api.get(`/api/provider/tenants/${route.params.id}`)
  loadUsers()
}

async function loadUsers() {
  users.value = await api.get(`/api/provider/tenants/${route.params.id}/users`)
}

async function remove() {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: tn.value.slug }), t('common.confirmTitle'), { type: 'warning', confirmButtonText: t('common.yesDelete') })
    await api.del(`/api/provider/tenants/${tn.value.id}`)
    router.push('/provider/tenants')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorText(e))
  }
}

async function removeUser(u) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: u.username }), t('common.confirmTitle'), { type: 'warning' })
    await api.del(`/api/provider/tenants/${tn.value.id}/users/${u.id}`)
    loadUsers()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>

<style scoped>
.role { margin: 0 6px 4px 0; }
h1 { display: flex; align-items: center; gap: 10px; }
</style>
