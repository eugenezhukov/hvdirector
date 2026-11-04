<template>
  <div class="login-page">
    <div class="login-box">
      <div class="brand">
        <img src="/favicon.svg" alt="" />
        <div>
          <div class="name">{{ t('product') }}</div>
          <div class="sub">{{ t('productSub') }}</div>
        </div>
      </div>
      <h2>{{ fixedOrg ? t('login.tenantTitle', { org: fixedOrg }) : t('login.title') }}</h2>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item v-if="!fixedOrg" :label="t('login.organization')">
          <el-input v-model="org" autocomplete="organization" :placeholder="t('login.orgHint')" clearable />
        </el-form-item>
        <el-form-item :label="t('common.username')">
          <el-input v-model="username" autocomplete="username" autofocus />
        </el-form-item>
        <el-form-item :label="t('common.password')">
          <el-input v-model="password" type="password" autocomplete="current-password" show-password />
        </el-form-item>
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="err" />
        <el-button type="primary" native-type="submit" :loading="busy" class="submit">{{ t('login.submit') }}</el-button>
      </el-form>
      <div class="footer">
        <router-link v-if="fixedOrg" to="/login">{{ t('login.switchToProvider') }}</router-link>
        <span v-else />
        <el-radio-group :model-value="currentLocale" size="small" @change="setLocale">
          <el-radio-button value="ru">RU</el-radio-button>
          <el-radio-button value="en">EN</el-radio-button>
        </el-radio-group>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuth } from '../stores/auth'
import { currentLocale, errorText, setLocale } from '../i18n'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const auth = useAuth()

const fixedOrg = computed(() => route.params.org || '')
const org = ref('')
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  busy.value = true
  try {
    const keepLocale = currentLocale.value
    await auth.login(username.value.trim(), password.value, (fixedOrg.value || org.value).trim())
    await auth.saveLocale(keepLocale)
    router.push(auth.home)
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background: radial-gradient(circle at 20% 20%, #1b5a7e, #0b2d42 60%);
}
.login-box { width: 400px; background: #fff; border-radius: 10px; padding: 28px 32px; box-shadow: 0 12px 40px rgba(0, 0, 0, .35); }
.brand { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
.brand img { width: 44px; height: 44px; }
.brand .name { font-size: 22px; font-weight: 600; color: #0b2d42; }
.brand .sub { color: #6b7a87; font-size: 13px; }
h2 { font-size: 17px; font-weight: 500; margin: 0 0 16px; }
.submit { width: 100%; margin-top: 4px; }
.err { margin-bottom: 12px; }
.footer { display: flex; justify-content: space-between; align-items: center; margin-top: 18px; font-size: 13px; }
.footer a { color: var(--el-color-primary); text-decoration: none; }
</style>
