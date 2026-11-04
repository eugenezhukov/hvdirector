<template>
  <span>
    <input ref="file" type="file" accept=".iso" hidden @change="onFile" />
    <el-button type="primary" :disabled="busy" @click="file.click()"><el-icon><IconUpload /></el-icon>{{ t('isos.upload') }}</el-button>
    <el-dialog v-model="busy" :title="t('isos.uploading', { name: current })" width="520px" :close-on-click-modal="false" :show-close="false">
      <el-progress :percentage="percent" :stroke-width="16" text-inside />
      <div class="muted info">{{ fmtBytes(done) }} / {{ fmtBytes(total) }} · {{ speed }}</div>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <template #footer>
        <el-button type="danger" plain @click="cancel">{{ t('isos.cancelUpload') }}</el-button>
      </template>
    </el-dialog>
  </span>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { errorText } from '../i18n'
import { fmtBytes } from '../utils/format'

const props = defineProps({ baseUrl: { type: String, required: true } })
const emit = defineEmits(['uploaded', 'changed'])
const { t } = useI18n()
const CHUNK = 16 * 1024 * 1024
const file = ref(null)
const busy = ref(false)
const current = ref('')
const done = ref(0)
const total = ref(0)
const error = ref('')
const speed = ref('')
let cancelled = false
let isoId = null
const percent = computed(() => (total.value ? Math.floor((done.value / total.value) * 100) : 0))

async function onFile(ev) {
  const f = ev.target.files[0]
  ev.target.value = ''
  if (!f) return
  cancelled = false
  error.value = ''
  current.value = f.name
  done.value = 0
  total.value = f.size
  speed.value = ''
  try {
    const iso = await api.post(props.baseUrl, { name: f.name, size_bytes: f.size })
    isoId = iso.id
    busy.value = true
    emit('changed')
    const started = Date.now()
    let offset = 0
    while (offset < f.size && !cancelled) {
      let attempt = 0
      for (;;) {
        try {
          const res = await api.put(`${props.baseUrl}/${iso.id}/data?offset=${offset}`, f.slice(offset, offset + CHUNK))
          offset = res.uploaded_bytes
          break
        } catch (e) {
          if (e.code === 'iso_bad_offset' && e.params && e.params.expected !== undefined) {
            offset = e.params.expected
            break
          }
          if (++attempt > 3 || cancelled) throw e
          await new Promise((r) => setTimeout(r, 1000 * attempt))
        }
      }
      done.value = offset
      const sec = (Date.now() - started) / 1000
      if (sec > 0) speed.value = `${fmtBytes(offset / sec)}/s`
    }
    if (!cancelled) {
      busy.value = false
      ElMessage.success(t('isos.uploadDone'))
      emit('uploaded')
    }
  } catch (e) {
    error.value = errorText(e)
    if (!busy.value) ElMessage.error(error.value)
  }
}

async function cancel() {
  cancelled = true
  busy.value = false
  if (isoId) {
    try {
      await api.del(`${props.baseUrl}/${isoId}`)
    } catch {
      /* already gone */
    }
  }
  emit('changed')
}
</script>

<style scoped>
.info { margin: 10px 0; font-size: 13px; }
</style>
