<template>
  <div class="quota">
    <div class="row">
      <span class="label">{{ label }}</span>
      <span class="val">
        <b>{{ fmt(used) }}</b>{{ ' ' }}
        <span v-if="limit !== null && limit !== undefined" class="of">{{ t('common.of') }} {{ fmt(limit) }}</span>
        <span v-else class="of muted">· {{ t('quota.unlimited') }}</span>
      </span>
    </div>
    <el-progress
      :percentage="limit === null || limit === undefined ? 0 : percent"
      :status="percent >= 100 ? 'exception' : percent >= 85 ? 'warning' : undefined"
      :show-text="false"
      :stroke-width="8"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { pct } from '../utils/format'

const props = defineProps({
  label: String,
  used: { type: Number, default: 0 },
  limit: { type: Number, default: null },
  format: { type: Function, default: (v) => v },
})
const { t } = useI18n()
const percent = computed(() => pct(props.used, props.limit))
const fmt = (v) => props.format(v)
</script>

<style scoped>
.quota { margin-bottom: 14px; }
.row { display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 4px; gap: 8px; }
.label { color: #3c4b57; }
</style>
