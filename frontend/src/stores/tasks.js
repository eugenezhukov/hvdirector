import { defineStore } from 'pinia'
import { ElNotification } from 'element-plus'
import { api } from '../api'
import { i18n } from '../i18n'
import { taskKind } from '../utils/format'

const FINAL = new Set(['success', 'error'])

export const useTasks = defineStore('tasks', {
  state: () => ({ items: [], timer: null, watchers: {}, open: false, lastFetch: 0 }),
  getters: {
    active: (s) => s.items.filter((t) => !FINAL.has(t.status)).length,
  },
  actions: {
    async refresh() {
      try {
        this.items = await api.get('/api/tasks?limit=25')
        this.lastFetch = Date.now()
      } catch {
        /* ignore polling errors */
      }
      for (const id of Object.keys(this.watchers)) {
        let t = this.items.find((x) => x.id === id)
        if (!t) {
          try {
            t = await api.get(`/api/tasks/${id}`)
          } catch (e) {
            if (e.status === 404) {
              // not a task (or not visible to this user): stop watching it
              const w = this.watchers[id]
              delete this.watchers[id]
              if (w) w({ id, status: 'error', kind: '', message: 'not found' })
            }
            continue
          }
        }
        const w = this.watchers[id]
        if (w && FINAL.has(t.status)) {
          delete this.watchers[id]
          w(t)
        }
      }
    },
    start() {
      if (this.timer) return
      const tick = async () => {
        const busy = this.active > 0 || Object.keys(this.watchers).length > 0
        if (busy || Date.now() - this.lastFetch > 15000) await this.refresh()
        this.timer = setTimeout(tick, busy ? 1500 : 3000)
      }
      tick()
    },
    stop() {
      clearTimeout(this.timer)
      this.timer = null
      this.items = []
      this.watchers = {}
    },
    // Wait for a task to finish; shows a notification. Resolves with the final task.
    track(task, { notify = true, label = null } = {}) {
      if (!task || !task.id) return Promise.resolve(task)
      if (!this.items.find((t) => t.id === task.id)) this.items.unshift(task)
      return new Promise((resolve) => {
        this.watchers[task.id] = (final) => {
          if (notify) {
            const t = i18n.global.t
            const name = label || `${taskKind(final.kind)} ${final.target_name || ''}`.trim()
            if (final.status === 'success') {
              ElNotification({ type: 'success', title: t('tasks.done'), message: name, duration: 3000 })
            } else {
              ElNotification({ type: 'error', title: t('tasks.failed'), message: `${name}: ${final.message}`, duration: 0 })
            }
          }
          resolve(final)
        }
        this.refresh()
      })
    },
  },
})
