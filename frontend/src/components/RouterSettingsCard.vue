<template>
  <div v-if="s" class="card">
    <h3>{{ t('routers.settings') }}</h3>
    <el-form label-width="290px">
      <el-form-item :label="t('routers.iso')">
        <el-select v-model="s.iso_id" clearable filterable style="width: 100%">
          <el-option v-for="i in isos" :key="i.id" :value="i.id" :label="i.name" />
        </el-select>
      </el-form-item>
      <el-form-item :label="`${t('routers.vcpu')} / ${t('routers.memory')} / ${t('routers.disk')}`">
        <el-input-number v-model="s.vcpu" :min="1" :max="16" controls-position="right" style="width: 100px" />
        <el-input-number v-model="s.memory_mb" :min="512" :step="512" controls-position="right" style="width: 130px; margin-left: 8px" />
        <el-input-number v-model="s.disk_gb" :min="4" controls-position="right" style="width: 110px; margin-left: 8px" />
      </el-form-item>
      <el-form-item :label="t('routers.mirror')">
        <el-input v-model="s.mirror_host" placeholder="deb.debian.org" />
        <div class="form-hint">{{ t('routers.mirrorHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('routers.mirrorDir')"><el-input v-model="s.mirror_dir" placeholder="/debian" /></el-form-item>
      <el-form-item :label="t('routers.proxy')"><el-input v-model="s.http_proxy" placeholder="http://proxy:3128" /></el-form-item>
      <el-form-item :label="t('routers.timezone')"><el-input v-model="s.timezone" placeholder="Europe/Moscow" /></el-form-item>
      <el-form-item :label="t('routers.mgmtAllowed')">
        <el-input v-model="s.mgmt_allowed" placeholder="10.0.0.50, 10.0.10.0/24" />
        <div class="form-hint">{{ t('routers.mgmtAllowedHint') }}</div>
      </el-form-item>
      <el-form-item :label="t('routers.keys')"><el-input v-model="s.authorized_keys" type="textarea" :rows="3" placeholder="ssh-ed25519 AAAA... admin@corp" /></el-form-item>
      <el-form-item :label="t('routers.timeout')"><el-input-number v-model="s.install_timeout_min" :min="10" :max="240" controls-position="right" /></el-form-item>
      <el-form-item :label="t('routers.publicKey')">
        <el-input :model-value="publicKey" readonly type="textarea" :rows="2" class="mono" />
      </el-form-item>
      <el-form-item><el-button type="primary" @click="save">{{ t('common.save') }}</el-button></el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { errorText } from '../i18n'

const { t } = useI18n()
const s = ref(null)
const isos = ref([])
const publicKey = ref('')

async function load() {
  const [key, all] = await Promise.all([api.get('/api/provider/router-settings/ssh-key'), api.get('/api/provider/isos')])
  s.value = key.settings
  publicKey.value = key.public_key
  isos.value = all.filter((i) => i.public && i.status === 'ready')
}

async function save() {
  try {
    const body = { ...s.value }
    delete body.has_ssh_private_key
    delete body.ssh_public_key
    s.value = await api.put('/api/provider/settings/router', body)
    ElMessage.success(t('common.saved'))
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>
