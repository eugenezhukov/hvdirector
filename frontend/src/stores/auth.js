import { defineStore } from 'pinia'
import { api, setCsrf } from '../api'
import { setLocale } from '../i18n'

export const useAuth = defineStore('auth', {
  state: () => ({ me: null, loaded: false }),
  getters: {
    isProvider: (s) => !!(s.me && s.me.user.is_provider),
    tenant: (s) => (s.me ? s.me.tenant : null),
    perms: (s) => new Set(s.me ? s.me.permissions : []),
    home() {
      if (!this.me) return '/login'
      return this.isProvider ? '/provider' : `/tenant/${this.me.tenant.slug}`
    },
  },
  actions: {
    can(perm) {
      return this.perms.has(perm)
    },
    apply(me) {
      this.me = me
      this.loaded = true
      setCsrf(me ? me.csrf_token : '')
      if (me && me.user.locale) setLocale(me.user.locale)
    },
    async load() {
      try {
        this.apply(await api.get('/api/auth/me', { noAuthRedirect: true }))
      } catch {
        this.apply(null)
      }
      return this.me
    },
    async login(username, password, org) {
      const me = await api.post('/api/auth/login', { username, password, org: org || null })
      this.apply(me)
      return me
    },
    async logout() {
      try {
        await api.post('/api/auth/logout')
      } finally {
        const org = this.me && this.me.tenant ? this.me.tenant.slug : null
        this.apply(null)
        this.loaded = true
        return org
      }
    },
    async saveLocale(locale) {
      setLocale(locale)
      if (this.me) {
        this.me.user.locale = locale
        try {
          await api.put('/api/auth/locale', { locale })
        } catch {
          /* not critical */
        }
      }
    },
  },
})
