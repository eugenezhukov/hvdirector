import { createI18n } from 'vue-i18n'
import { ref } from 'vue'
import ru from './ru'
import en from './en'

const saved = (() => {
  try {
    return localStorage.getItem('hvd.locale')
  } catch {
    return null
  }
})()

const initial = saved === 'en' || saved === 'ru' ? saved : (navigator.language || 'ru').startsWith('en') ? 'en' : 'ru'

export const i18n = createI18n({
  legacy: false,
  locale: initial,
  fallbackLocale: 'en',
  messages: { ru, en },
  missingWarn: false,
  fallbackWarn: false,
})

export const currentLocale = ref(initial)

export function setLocale(locale) {
  if (locale !== 'ru' && locale !== 'en') return
  i18n.global.locale.value = locale
  currentLocale.value = locale
  document.documentElement.lang = locale
  try {
    localStorage.setItem('hvd.locale', locale)
  } catch {
    /* private mode */
  }
}

// Translate an ApiError into a human readable message.
export function errorText(err) {
  const t = i18n.global.t
  const te = i18n.global.te
  if (!err) return ''
  const code = err.code || 'http_error'
  const params = { ...(err.params || {}) }
  if (params.resource) {
    const r = String(params.resource)
    params.resource = r.startsWith('storage:') ? t('quota.storageOf', { name: r.slice(8) }) : te('quota.' + r) ? t('quota.' + r) : r
  }
  if (te('errors.' + code)) return t('errors.' + code, params)
  return err.message || code
}
