<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('tenants.title') }}</h1>
      <el-button type="primary" @click="dlg.open()"><el-icon><IconPlus /></el-icon>{{ t('tenants.add') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items" :empty-text="t('common.noData')" class="clickable" @row-click="(r) => router.push(`/provider/tenants/${r.id}`)">
        <el-table-column :label="t('tenants.slug')" width="150">
          <template #default="{ row }"><span class="link mono">{{ row.slug }}</span></template>
        </el-table-column>
        <el-table-column prop="name" :label="t('common.name')" min-width="180" />
        <el-table-column prop="cluster_name" :label="t('tenants.cluster')" width="160" />
        <el-table-column :label="t('tenants.quotas')" min-width="260">
          <template #default="{ row }">
            <span class="muted small">
              vCPU {{ row.quota_vcpu ?? '∞' }} · RAM {{ row.quota_memory_mb === null ? '∞' : fmtMb(row.quota_memory_mb) }} ·
              VM {{ row.quota_vms ?? '∞' }} · snap {{ row.quota_snapshots ?? '∞' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="t('tenants.vlans')" width="150"><template #default="{ row }"><span class="mono">{{ row.vlans || '—' }}</span></template></el-table-column>
        <el-table-column prop="vm_count" :label="t('tenants.vms')" width="80" />
        <el-table-column prop="user_count" :label="t('tenants.users')" width="110" />
        <el-table-column :label="t('common.status')" width="110">
          <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag></template>
        </el-table-column>
      </el-table>
    </div>
    <TenantDialog ref="dlg" @saved="load" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../api'
import TenantDialog from '../../components/TenantDialog.vue'
import { fmtMb } from '../../utils/format'

const { t } = useI18n()
const router = useRouter()
const items = ref([])
const loading = ref(false)
const dlg = ref(null)

async function load() {
  loading.value = true
  try {
    items.value = await api.get('/api/provider/tenants')
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.clickable :deep(tr) { cursor: pointer; }
.small { font-size: 12px; }
</style>
