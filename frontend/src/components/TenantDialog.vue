<template>
  <el-dialog v-model="visible" :title="form.id ? `${t('common.edit')}: ${form.slug}` : t('tenants.add')" width="760px" top="5vh" :close-on-click-modal="false">
    <el-form label-width="230px">
      <h4>{{ t('tenants.general') }}</h4>
      <el-form-item :label="t('tenants.slug')" required>
        <el-input v-model="form.slug" :disabled="!!form.id" placeholder="acme" style="width: 220px" />
        <div class="form-hint">{{ t('tenants.slugHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('common.name')" required><el-input v-model="form.name" /></el-form-item>
      <el-form-item :label="t('common.description')"><el-input v-model="form.description" /></el-form-item>
      <el-form-item :label="t('tenants.cluster')" required>
        <el-select v-model="form.cluster_id" style="width: 320px">
          <el-option v-for="c in clusters" :key="c.id" :value="c.id" :label="`${c.name} (${c.kind === 'cluster' ? t('clusters.kindCluster') : t('clusters.kindStandalone')})`" />
        </el-select>
      </el-form-item>
      <el-form-item :label="t('tenants.enabled')"><el-switch v-model="form.enabled" /></el-form-item>

      <h4>{{ t('tenants.quotas') }}</h4>
      <el-form-item v-for="q in quotaFields" :key="q.key" :label="q.label">
        <el-input-number v-model="form[q.key]" :min="0" :disabled="form[q.key] === null" controls-position="right" style="width: 160px" />
        <el-checkbox :model-value="form[q.key] === null" class="ml" @change="(v) => (form[q.key] = v ? null : q.def)">
          {{ t('common.unlimited') }}
        </el-checkbox>
      </el-form-item>

      <h4>{{ t('tenants.storageQuotas') }}</h4>
      <div v-for="(sq, i) in form.storage_quotas" :key="i" class="sq-row">
        <el-select v-model="sq.storage_class_id" style="width: 230px">
          <el-option v-for="c in classes" :key="c.id" :value="c.id" :label="`${c.name} (tier ${c.tier})`"
                     :disabled="form.storage_quotas.some((x, j) => j !== i && x.storage_class_id === c.id)" />
        </el-select>
        <el-input-number v-model="sq.limit_gb" :min="0" :disabled="sq.limit_gb === null" controls-position="right" style="width: 150px" />
        <span class="muted">{{ t('common.gb') }}</span>
        <el-checkbox :model-value="sq.limit_gb === null" @change="(v) => (sq.limit_gb = v ? null : 500)">{{ t('common.unlimited') }}</el-checkbox>
        <el-button link type="danger" @click="form.storage_quotas.splice(i, 1)"><el-icon><IconDelete /></el-icon></el-button>
      </div>
      <el-button size="small" :disabled="form.storage_quotas.length >= classes.length" @click="addClass" class="mb">
        <el-icon><IconPlus /></el-icon>{{ t('tenants.addClass') }}
      </el-button>

      <h4>{{ t('tenants.network') }}</h4>
      <el-form-item :label="t('tenants.vlans')">
        <el-input v-model="form.vlans" placeholder="100-110, 120" />
        <div class="form-hint">{{ t('tenants.vlansHint') }} · {{ t('tenants.vlanCount', { n: vlanCount }) }}</div>
      </el-form-item>

      <template v-if="!form.id">
        <h4>{{ t('tenants.admin') }}</h4>
        <el-form-item :label="t('common.username')"><el-input v-model="form.admin_username" placeholder="admin" style="width: 240px" /></el-form-item>
        <el-form-item :label="t('common.fullName')"><el-input v-model="form.admin_full_name" /></el-form-item>
        <el-form-item :label="t('common.password')"><el-input v-model="form.admin_password" type="password" show-password style="width: 240px" /></el-form-item>
      </template>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    </el-form>
    <template #footer>
      <el-button @click="visible = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="busy" @click="save">{{ t('common.save') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api'
import { errorText } from '../i18n'

const emit = defineEmits(['saved'])
const { t } = useI18n()
const visible = ref(false)
const busy = ref(false)
const error = ref('')
const clusters = ref([])
const classes = ref([])
const form = reactive({})

const quotaFields = computed(() => [
  { key: 'quota_vcpu', label: t('tenants.vcpu'), def: 16 },
  { key: 'quota_memory_gb', label: t('tenants.memoryGb'), def: 64 },
  { key: 'quota_vms', label: t('tenants.vms'), def: 10 },
  { key: 'quota_iso_gb', label: t('tenants.isoGb'), def: 50 },
  { key: 'quota_snapshots', label: t('tenants.snapshots'), def: 20 },
])

const vlanCount = computed(() => {
  let n = 0
  for (const part of (form.vlans || '').split(/[,;\s]+/)) {
    const m = part.match(/^(\d+)(?:-(\d+))?$/)
    if (m) n += Math.abs((m[2] ? +m[2] : +m[1]) - +m[1]) + 1
  }
  return n
})

function addClass() {
  const free = classes.value.find((c) => !form.storage_quotas.some((x) => x.storage_class_id === c.id))
  if (free) form.storage_quotas.push({ storage_class_id: free.id, limit_gb: 500 })
}

async function open(tenant = null) {
  error.value = ''
  ;[clusters.value, classes.value] = await Promise.all([api.get('/api/provider/clusters'), api.get('/api/provider/storage-classes')])
  if (tenant) {
    Object.assign(form, {
      id: tenant.id, slug: tenant.slug, name: tenant.name, description: tenant.description, cluster_id: tenant.cluster_id,
      enabled: tenant.enabled, quota_vcpu: tenant.quota_vcpu,
      quota_memory_gb: tenant.quota_memory_mb === null ? null : Math.round(tenant.quota_memory_mb / 1024),
      quota_vms: tenant.quota_vms, quota_iso_gb: tenant.quota_iso_gb, quota_snapshots: tenant.quota_snapshots,
      vlans: tenant.vlans, storage_quotas: tenant.storage_quotas.map((q) => ({ storage_class_id: q.storage_class_id, limit_gb: q.limit_gb })),
    })
  } else {
    Object.assign(form, {
      id: null, slug: '', name: '', description: '', cluster_id: clusters.value[0]?.id || null, enabled: true,
      quota_vcpu: 16, quota_memory_gb: 64, quota_vms: 10, quota_iso_gb: 50, quota_snapshots: 20, vlans: '',
      storage_quotas: classes.value.slice(0, 1).map((c) => ({ storage_class_id: c.id, limit_gb: 500 })),
      admin_username: 'admin', admin_full_name: '', admin_password: '',
    })
  }
  visible.value = true
}

async function save() {
  busy.value = true
  error.value = ''
  try {
    const body = {
      name: form.name, description: form.description, cluster_id: form.cluster_id, enabled: form.enabled,
      quota_vcpu: form.quota_vcpu, quota_memory_mb: form.quota_memory_gb === null ? null : form.quota_memory_gb * 1024,
      quota_vms: form.quota_vms, quota_iso_gb: form.quota_iso_gb, quota_snapshots: form.quota_snapshots,
      vlans: form.vlans, storage_quotas: form.storage_quotas,
    }
    let res
    if (form.id) res = await api.patch(`/api/provider/tenants/${form.id}`, body)
    else {
      res = await api.post('/api/provider/tenants', {
        ...body, slug: form.slug.trim().toLowerCase(), admin_username: form.admin_username || null,
        admin_password: form.admin_password || null, admin_full_name: form.admin_full_name || null,
      })
    }
    visible.value = false
    emit('saved', res)
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}

defineExpose({ open })
</script>

<style scoped>
h4 { margin: 18px 0 10px; font-size: 14px; color: #0b2d42; border-bottom: 1px solid var(--hvd-border); padding-bottom: 4px; }
.ml { margin-left: 14px; }
.mb { margin-bottom: 8px; }
.sq-row { display: flex; align-items: center; gap: 10px; margin: 0 0 10px 230px; }
</style>
