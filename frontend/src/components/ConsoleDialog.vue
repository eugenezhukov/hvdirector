<template>
  <el-dialog
    v-model="visible"
    :title="t('vms.consoleTitle', { name: vmName })"
    width="1100px"
    top="4vh"
    draggable
    destroy-on-close
    class="console-dialog"
    @closed="src = ''"
  >
    <div class="frame-wrap">
      <iframe v-if="src" ref="frame" :src="src" class="frame" allow="fullscreen; clipboard-read; clipboard-write" />
    </div>
    <template #footer>
      <el-button @click="openWindow"><el-icon><IconTopRight /></el-icon>{{ t('vms.openNewWindow') }}</el-button>
      <el-button type="primary" @click="visible = false">{{ t('common.close') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { errorText, currentLocale } from '../i18n'

const { t } = useI18n()
const visible = ref(false)
const src = ref('')
const vmName = ref('')
const vmId = ref('')

async function ticketUrl(id, name) {
  const res = await api.post(`/api/tenant/vms/${id}/console`)
  const q = new URLSearchParams({ ticket: res.ticket, name, lang: currentLocale.value })
  return `/console.html?${q.toString()}`
}

async function open(vm) {
  try {
    vmId.value = vm.id
    vmName.value = vm.name
    src.value = await ticketUrl(vm.id, vm.name)
    visible.value = true
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function openWindow() {
  try {
    const url = await ticketUrl(vmId.value, vmName.value)
    visible.value = false
    window.open(url, `console-${vmId.value}`, 'width=1100,height=860,noopener')
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

defineExpose({ open })
</script>

<style scoped>
.frame-wrap { height: 72vh; background: #000; border-radius: 4px; overflow: hidden; }
.frame { width: 100%; height: 100%; border: 0; display: block; }
</style>
