<template>
  <span class="vm-state">
    <el-tag v-if="vm.status !== 'ready'" :type="statusType(vm.status)" size="small" effect="light">
      <el-icon v-if="vm.status === 'creating' || vm.status === 'deleting'" class="spin"><IconLoading /></el-icon>
      {{ t('vmStatus.' + vm.status) }}
    </el-tag>
    <el-tag v-else :type="stateType(vm.power_state)" size="small" effect="light">
      <span class="dot" :class="vm.power_state" />{{ stateText(vm.power_state) }}
    </el-tag>
  </span>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import { stateText, stateType, statusType } from '../utils/format'

defineProps({ vm: { type: Object, required: true } })
const { t } = useI18n()
</script>

<style scoped>
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #9aa7b3; margin-right: 6px; }
.dot.Running { background: var(--el-color-success); }
.dot.Saved, .dot.Paused { background: var(--el-color-warning); }
.spin { animation: spin 1.2s linear infinite; margin-right: 4px; vertical-align: middle; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
