<template>
  <div class="task-panel" :class="{ open: tasks.open }">
    <div class="bar" @click="tasks.open = !tasks.open">
      <el-icon><component :is="tasks.open ? 'IconArrowDown' : 'IconArrowUp'" /></el-icon>
      <span class="title">{{ t('tasks.recent') }}</span>
      <el-tag v-if="tasks.active" size="small" type="primary" effect="dark" round>
        {{ t('tasks.activeCount', { n: tasks.active }) }}
      </el-tag>
      <span v-if="last" class="last">
        <el-icon :class="['ic', last.status]"><component :is="icon(last.status)" /></el-icon>
        {{ taskKind(last.kind) }} — {{ last.target_name }}
      </span>
    </div>
    <div v-if="tasks.open" class="list">
      <el-table :data="tasks.items" size="small" height="230" :empty-text="t('tasks.empty')">
        <el-table-column :label="t('tasks.kind')" min-width="170">
          <template #default="{ row }">
            <el-icon :class="['ic', row.status]"><component :is="icon(row.status)" /></el-icon>
            {{ taskKind(row.kind) }}
          </template>
        </el-table-column>
        <el-table-column prop="target_name" :label="t('tasks.target')" min-width="140" />
        <el-table-column :label="t('common.status')" width="190">
          <template #default="{ row }">
            <el-progress v-if="row.status === 'running'" :percentage="row.progress" :stroke-width="10" />
            <el-tag v-else :type="statusType(row.status)" size="small">{{ t('tasks.' + row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="message" :label="t('tasks.message')" min-width="220" show-overflow-tooltip />
        <el-table-column prop="username" :label="t('tasks.user')" width="120" />
        <el-table-column :label="t('tasks.started')" width="170">
          <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTasks } from '../stores/tasks'
import { fmtDate, statusType, taskKind } from '../utils/format'

const { t } = useI18n()
const tasks = useTasks()
const last = computed(() => tasks.items[0])

function icon(status) {
  return { success: 'IconCircleCheck', error: 'IconCircleClose', running: 'IconLoading', queued: 'IconClock' }[status] || 'IconClock'
}
</script>

<style scoped>
.task-panel {
  position: fixed; left: 230px; right: 0; bottom: 0; background: #fff; border-top: 1px solid var(--hvd-border);
  box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.06); z-index: 20;
}
.bar { height: 36px; display: flex; align-items: center; gap: 10px; padding: 0 16px; cursor: pointer; font-size: 13px; }
.title { font-weight: 600; }
.last { color: #6b7a87; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; display: flex; align-items: center; gap: 4px; }
.list { padding: 0 8px 8px; }
.ic { vertical-align: middle; margin-right: 4px; }
.ic.success { color: var(--el-color-success); }
.ic.error { color: var(--el-color-danger); }
.ic.running { color: var(--el-color-primary); animation: spin 1.2s linear infinite; }
.ic.queued { color: #8a98a5; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
