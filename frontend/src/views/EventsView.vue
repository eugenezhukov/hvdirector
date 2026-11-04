<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('events.title') }}</h1>
      <el-input v-model="q" :placeholder="t('events.searchHint')" clearable style="width: 300px" @keyup.enter="reload" @clear="reload">
        <template #prefix><el-icon><IconSearch /></el-icon></template>
      </el-input>
      <el-button @click="reload"><el-icon><IconRefresh /></el-icon>{{ t('common.refresh') }}</el-button>
    </div>
    <div class="card">
      <el-table v-loading="loading" :data="items" :empty-text="t('common.noData')" size="small">
        <el-table-column :label="t('events.ts')" width="170">
          <template #default="{ row }">{{ fmtDate(row.ts) }}</template>
        </el-table-column>
        <el-table-column prop="username" :label="t('events.user')" width="140" />
        <el-table-column prop="action" :label="t('events.action')" width="210">
          <template #default="{ row }"><span class="mono">{{ row.action }}</span></template>
        </el-table-column>
        <el-table-column :label="t('events.target')" min-width="180">
          <template #default="{ row }">
            <span class="muted">{{ row.target_type }}</span> {{ row.target_name }}
          </template>
        </el-table-column>
        <el-table-column :label="t('events.result')" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">{{ t('events.' + row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.details')" min-width="220" show-overflow-tooltip>
          <template #default="{ row }"><span class="mono">{{ details(row.details) }}</span></template>
        </el-table-column>
        <el-table-column prop="ip" :label="t('events.ip')" width="130" />
      </el-table>
      <div v-if="hasMore" style="text-align: center; margin-top: 12px">
        <el-button @click="more">{{ t('common.loadMore') }}</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api'
import { fmtDate } from '../utils/format'

const { t } = useI18n()
const items = ref([])
const loading = ref(false)
const hasMore = ref(false)
const q = ref('')
const PAGE = 100

function details(d) {
  if (!d || !Object.keys(d).length) return ''
  return JSON.stringify(d)
}

async function fetchPage(offset) {
  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) })
  if (q.value) params.set('q', q.value)
  return api.get(`/api/events?${params}`)
}

async function reload() {
  loading.value = true
  try {
    const res = await fetchPage(0)
    items.value = res.items
    hasMore.value = res.has_more
  } finally {
    loading.value = false
  }
}

async function more() {
  const res = await fetchPage(items.value.length)
  items.value = items.value.concat(res.items)
  hasMore.value = res.has_more
}

onMounted(reload)
</script>
