<template>
  <el-config-provider :locale="elLocale">
    <router-view />
  </el-config-provider>
</template>

<script setup>
import { computed } from 'vue'
import ruLocale from 'element-plus/es/locale/lang/ru'
import enLocale from 'element-plus/es/locale/lang/en'
import { currentLocale } from './i18n'

const elLocale = computed(() => (currentLocale.value === 'ru' ? ruLocale : enLocale))
</script>
