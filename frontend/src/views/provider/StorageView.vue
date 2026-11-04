<template>
  <div class="page">
    <div class="page-head">
      <div style="flex: 1"><h1>{{ t('storage.title') }}</h1><div class="sub">{{ t('storage.hint') }}</div></div>
      <el-button type="primary" @click="openNew"><el-icon><IconPlus /></el-icon>{{ t('storage.add') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items" :empty-text="t('common.noData')">
        <el-table-column prop="tier" :label="t('storage.tier')" width="110" />
        <el-table-column :label="t('common.name')" min-width="160">
          <template #default="{ row }"><b>{{ row.name }}</b><div class="muted">{{ row.description }}</div></template>
        </el-table-column>
        <el-table-column :label="t('storage.datastores')" min-width="260">
          <template #default="{ row }">
            <div v-for="d in row.datastores" :key="d.id" class="mono">{{ d.cluster }}: {{ d.path }}</div>
            <span v-if="!row.datastores.length" class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('storage.capacity')" width="120"><template #default="{ row }">{{ fmtGb(row.capacity_gb) }}</template></el-table-column>
        <el-table-column :label="t('storage.free')" width="120"><template #default="{ row }">{{ fmtGb(row.free_gb) }}</template></el-table-column>
        <el-table-column :label="t('storage.allocated')" width="140"><template #default="{ row }">{{ fmtGb(row.allocated_gb) }}</template></el-table-column>
        <el-table-column width="110">
          <template #default="{ row }">
            <el-button link @click="openEdit(row)"><el-icon><IconEdit /></el-icon></el-button>
            <el-button link type="danger" @click="remove(row)"><el-icon><IconDelete /></el-icon></el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="dialog" :title="form.id ? t('common.edit') : t('storage.add')" width="480px">
      <el-form label-width="190px">
        <el-form-item :label="t('common.name')"><el-input v-model="form.name" placeholder="Gold SSD" /></el-form-item>
        <el-form-item :label="t('storage.tier')"><el-input-number v-model="form.tier" :min="1" :max="100" /></el-form-item>
        <el-form-item :label="t('common.description')"><el-input v-model="form.description" type="textarea" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">{{ t('common.cancel') }}</el-button>
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
import { fmtGb } from '../../utils/format'

const { t } = useI18n()
const items = ref([])
const loading = ref(false)
const dialog = ref(false)
const form = reactive({})

async function load() {
  loading.value = true
  try {
    items.value = await api.get('/api/provider/storage-classes')
  } finally {
    loading.value = false
  }
}

function openNew() {
  Object.assign(form, { id: null, name: '', tier: 1, description: '' })
  dialog.value = true
}

function openEdit(r) {
  Object.assign(form, { id: r.id, name: r.name, tier: r.tier, description: r.description })
  dialog.value = true
}

async function save() {
  try {
    const body = { name: form.name, tier: form.tier, description: form.description }
    if (form.id) await api.patch(`/api/provider/storage-classes/${form.id}`, body)
    else await api.post('/api/provider/storage-classes', body)
    dialog.value = false
    load()
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function remove(r) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: r.name }), t('common.confirmTitle'), { type: 'warning' })
    await api.del(`/api/provider/storage-classes/${r.id}`)
    load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>
