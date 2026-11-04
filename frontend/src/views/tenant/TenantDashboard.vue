<template>
  <div v-if="s" class="page">
    <div class="page-head">
      <div style="flex: 1">
        <h1>{{ s.tenant.name }}</h1>
        <div class="sub">{{ t('tenant.dashTitle') }} · <span class="mono">{{ s.tenant.slug }}</span></div>
      </div>
      <el-button v-if="auth.can('vm.create')" type="primary" @click="router.push(base + '/vms')"><el-icon><IconPlus /></el-icon>{{ t('vms.create') }}</el-button>
    </div>
    <div class="tiles">
      <div class="tile link" @click="router.push(base + '/vms')"><div class="label">{{ t('tenant.vmsTotal') }}</div><div class="value">{{ s.vms_total }}</div></div>
      <div class="tile"><div class="label">{{ t('tenant.vmsRunning') }}</div><div class="value" style="color: #2e9e5b">{{ s.vms_running }}</div></div>
      <div class="tile"><div class="label">{{ t('tenant.vmsError') }}</div><div class="value" :class="{ 'danger-text': s.vms_error }">{{ s.vms_error }}</div></div>
      <div v-if="s.networks !== undefined" class="tile link" @click="router.push(base + '/networks')"><div class="label">{{ t('nav.networks') }}</div><div class="value">{{ s.networks }}</div></div>
    </div>
    <div class="grid-2">
      <div v-if="s.quotas" class="card">
        <h3>{{ t('quota.title') }}</h3>
        <QuotaBar :label="t('quota.vcpu')" :used="s.quotas.vcpu.used" :limit="s.quotas.vcpu.limit" />
        <QuotaBar :label="t('quota.memory')" :used="s.quotas.memory_mb.used" :limit="s.quotas.memory_mb.limit" :format="fmtMb" />
        <QuotaBar :label="t('quota.vms')" :used="s.quotas.vms.used" :limit="s.quotas.vms.limit" />
        <QuotaBar :label="t('quota.snapshots')" :used="s.quotas.snapshots.used" :limit="s.quotas.snapshots.limit" />
        <QuotaBar :label="t('quota.iso_gb')" :used="s.quotas.iso_gb.used" :limit="s.quotas.iso_gb.limit" :format="fmtGb" />
        <QuotaBar v-for="st in s.quotas.storage" :key="st.storage_class_id" :label="`${t('quota.storage')}: ${st.name}`"
                  :used="st.used_gb" :limit="st.limit_gb" :format="fmtGb" />
      </div>
      <div class="card">
        <h3>{{ t('tenant.yourAccess') }}</h3>
        <el-tag v-for="(r, i) in auth.me.roles" :key="i" class="role">
          {{ t('users.roleNames.' + r.role) }} · {{ r.vm_id ? vmName(r.vm_id) : t('users.scopeTenant') }}
        </el-tag>
        <p v-if="!s.quotas" class="muted">{{ t('tenant.delegatedHint') }}</p>
        <h3 style="margin-top: 18px">{{ t('nav.vms') }}</h3>
        <el-table :data="vms.slice(0, 8)" size="small" :empty-text="t('vms.noVms')" @row-click="(r) => router.push(`${base}/vms/${r.id}`)" class="clickable">
          <el-table-column prop="name" :label="t('common.name')" />
          <el-table-column :label="t('common.state')" width="160"><template #default="{ row }"><VmState :vm="row" /></template></el-table-column>
        </el-table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api'
import { useAuth } from '../../stores/auth'
import QuotaBar from '../../components/QuotaBar.vue'
import VmState from '../../components/VmState.vue'
import { fmtGb, fmtMb } from '../../utils/format'

const { t } = useI18n()
const router = useRouter()
const auth = useAuth()
const s = ref(null)
const vms = ref([])
const base = computed(() => `/tenant/${auth.tenant.slug}`)
const vmName = (id) => vms.value.find((v) => v.id === id)?.name || 'VM'

onMounted(async () => {
  ;[s.value, vms.value] = await Promise.all([api.get('/api/tenant/summary'), api.get('/api/tenant/vms')])
})
</script>

<style scoped>
.role { margin: 0 8px 8px 0; }
.clickable :deep(tr) { cursor: pointer; }
</style>
