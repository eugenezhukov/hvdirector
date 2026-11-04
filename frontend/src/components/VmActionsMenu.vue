<template>
  <el-dropdown trigger="click" @command="onCmd" @click.stop>
    <el-button size="small" @click.stop>
      {{ t('common.actions') }}<el-icon class="el-icon--right"><IconArrowDown /></el-icon>
    </el-button>
    <template #dropdown>
      <el-dropdown-menu>
        <template v-if="can('vm.power')">
          <el-dropdown-item command="start" :disabled="!ready || vm.power_state === 'Running'"><el-icon><IconVideoPlay /></el-icon>{{ t('power.start') }}</el-dropdown-item>
          <el-dropdown-item command="shutdown" :disabled="!ready || vm.power_state !== 'Running'"><el-icon><IconSwitchButton /></el-icon>{{ t('power.shutdown') }}</el-dropdown-item>
          <el-dropdown-item command="reboot" :disabled="!ready || vm.power_state !== 'Running'"><el-icon><IconRefreshRight /></el-icon>{{ t('power.reboot') }}</el-dropdown-item>
          <el-dropdown-item command="poweroff" :disabled="!ready || vm.power_state === 'Off'"><el-icon><IconCircleClose /></el-icon>{{ t('power.poweroff') }}</el-dropdown-item>
          <el-dropdown-item command="reset" :disabled="!ready || vm.power_state !== 'Running'"><el-icon><IconRefreshLeft /></el-icon>{{ t('power.reset') }}</el-dropdown-item>
        </template>
        <el-dropdown-item v-if="can('vm.console')" command="console" divided :disabled="!ready || vm.power_state !== 'Running'">
          <el-icon><IconMonitor /></el-icon>{{ t('vms.console') }}
        </el-dropdown-item>
        <el-dropdown-item v-if="can('vm.snapshot')" command="snapshot" :disabled="!ready"><el-icon><IconCamera /></el-icon>{{ t('vms.takeSnapshot') }}</el-dropdown-item>
        <el-dropdown-item v-if="can('vm.delete')" command="delete" divided :disabled="vm.status === 'creating' || vm.status === 'deleting'">
          <span class="danger-text"><el-icon><IconDelete /></el-icon>{{ t('common.delete') }}</span>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({ vm: { type: Object, required: true } })
const emit = defineEmits(['power', 'console', 'snapshot', 'delete'])
const { t } = useI18n()
const ready = computed(() => props.vm.status === 'ready')
const can = (p) => (props.vm.permissions || []).includes(p)

function onCmd(cmd) {
  if (['start', 'shutdown', 'reboot', 'poweroff', 'reset'].includes(cmd)) emit('power', cmd)
  else emit(cmd)
}
</script>
