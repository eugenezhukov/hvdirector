<template>
  <div class="page">
    <div class="page-head"><h1>{{ t('settings.title') }}</h1></div>
    <div v-if="s" class="grid-2">
      <div class="card">
        <h3>{{ t('settings.isoShare') }}</h3>
        <el-form label-width="250px" size="default">
          <el-form-item :label="t('settings.backend')">
            <el-radio-group v-model="s.iso_share.backend">
              <el-radio-button value="smb">{{ t('settings.backendSmb') }}</el-radio-button>
              <el-radio-button value="local">{{ t('settings.backendLocal') }}</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item :label="t('settings.unc')"><el-input v-model="s.iso_share.unc" placeholder="\\shared\iso" /></el-form-item>
          <template v-if="s.iso_share.backend === 'smb'">
            <el-form-item :label="t('settings.server')">
              <el-input v-model="s.iso_share.server" placeholder="10.0.0.5" />
              <div class="form-hint">{{ t('settings.serverHint') }}</div>
            </el-form-item>
            <el-form-item :label="t('settings.shareUser')"><el-input v-model="s.iso_share.username" placeholder="CORP\svc-iso" /></el-form-item>
            <el-form-item :label="t('common.password')">
              <el-input v-model="isoPassword" type="password" show-password :placeholder="s.iso_share.has_password ? '•••••• (' + t('settings.passwordSet') + ')' : ''" />
            </el-form-item>
          </template>
          <el-form-item v-else :label="t('settings.localPath')"><el-input v-model="s.iso_share.local_path" /></el-form-item>
          <el-form-item>
            <el-button type="primary" @click="save('iso_share')">{{ t('common.save') }}</el-button>
            <el-button @click="test('iso_share')">{{ t('common.test') }}</el-button>
          </el-form-item>
        </el-form>
      </div>
      <div>
        <div class="card">
          <h3>{{ t('settings.guacd') }}</h3>
          <el-form label-width="200px">
            <el-form-item :label="t('settings.guacdHost')"><el-input v-model="s.guacd.host" style="width: 220px" /></el-form-item>
            <el-form-item :label="t('settings.guacdPort')"><el-input-number v-model="s.guacd.port" :min="1" :max="65535" controls-position="right" /></el-form-item>
            <el-form-item :label="t('settings.keyboard')">
              <el-select v-model="s.console.keyboard_layout" style="width: 220px" filterable>
                <el-option v-for="k in layouts" :key="k" :value="k" :label="k" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveMany(['guacd', 'console'])">{{ t('common.save') }}</el-button>
              <el-button @click="test('guacd')">{{ t('common.test') }}</el-button>
            </el-form-item>
          </el-form>
        </div>
        <div class="card">
          <h3>{{ t('settings.sync') }} · {{ t('settings.placement') }}</h3>
          <el-form label-width="260px">
            <el-form-item :label="t('settings.interval')"><el-input-number v-model="s.sync.interval_s" :min="0" :max="3600" controls-position="right" /></el-form-item>
            <el-form-item :label="t('settings.reserve')"><el-input-number v-model="s.placement.memory_reserve_mb" :min="0" :step="1024" controls-position="right" /></el-form-item>
            <el-form-item><el-button type="primary" @click="saveMany(['sync', 'placement'])">{{ t('common.save') }}</el-button></el-form-item>
          </el-form>
        </div>
        <div class="card">
          <h3>{{ t('settings.security') }}</h3>
          <el-form label-width="290px">
            <el-form-item :label="t('settings.idle')"><el-input-number v-model="s.security.session_idle_minutes" :min="5" controls-position="right" /></el-form-item>
            <el-form-item :label="t('settings.maxHours')"><el-input-number v-model="s.security.session_max_hours" :min="1" controls-position="right" /></el-form-item>
            <el-form-item :label="t('settings.failedLogins')"><el-input-number v-model="s.security.max_failed_logins" :min="1" controls-position="right" /></el-form-item>
            <el-form-item :label="t('settings.lockout')"><el-input-number v-model="s.security.lockout_minutes" :min="1" controls-position="right" /></el-form-item>
            <el-form-item><el-button type="primary" @click="save('security')">{{ t('common.save') }}</el-button></el-form-item>
          </el-form>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '../../api'
import { errorText } from '../../i18n'

const { t } = useI18n()
const s = ref(null)
const isoPassword = ref('')
const layouts = ['en-us-qwerty', 'en-gb-qwerty', 'de-de-qwertz', 'de-ch-qwertz', 'fr-fr-azerty', 'fr-be-azerty',
  'fr-ca-qwerty', 'fr-ch-qwertz', 'cs-cz-qwertz', 'da-dk-qwerty', 'es-es-qwerty', 'es-latam-qwerty', 'hu-hu-qwertz',
  'it-it-qwerty', 'ja-jp-qwerty', 'no-no-qwerty', 'pl-pl-qwerty', 'pt-br-qwerty', 'pt-pt-qwerty', 'ro-ro-qwerty',
  'sv-se-qwerty', 'tr-tr-qwerty', 'failsafe']

async function load() {
  s.value = await api.get('/api/provider/settings')
}

async function save(key, quiet = false) {
  try {
    const body = { ...s.value[key] }
    if (key === 'iso_share' && isoPassword.value) body.password = isoPassword.value
    s.value[key] = await api.put(`/api/provider/settings/${key}`, body)
    if (key === 'iso_share') isoPassword.value = ''
    if (!quiet) ElMessage.success(t('common.saved'))
  } catch (e) {
    ElMessage.error(errorText(e))
    throw e
  }
}

async function saveMany(keys) {
  try {
    for (const k of keys) await save(k, true)
    ElMessage.success(t('common.saved'))
  } catch {
    /* error already shown */
  }
}

async function test(key) {
  try {
    if (key === 'iso_share') await save('iso_share', true)
    const res = await api.post(`/api/provider/settings/${key}/test`)
    if (res.ok) ElMessage.success(`${t('settings.testOk')}${res.version ? ' (' + res.version + ')' : ''}${res.free_gb ? ' · ' + res.free_gb + ' GB free' : ''}`)
    else ElMessage.error(t('settings.testFail', { error: res.error || '?' }))
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

onMounted(load)
</script>
