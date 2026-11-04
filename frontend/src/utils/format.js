import { i18n } from '../i18n'

const t = (...a) => i18n.global.t(...a)

export function fmtBytes(n) {
  if (n === null || n === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = Number(n)
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function fmtMb(mb) {
  if (mb === null || mb === undefined) return '—'
  return mb >= 1024 ? `${+(mb / 1024).toFixed(1)} ${t('common.gb')}` : `${mb} ${t('common.mb')}`
}

export function fmtGb(gb) {
  if (gb === null || gb === undefined) return '—'
  return gb >= 1024 ? `${+(gb / 1024).toFixed(2)} TB` : `${+Number(gb).toFixed(1)} ${t('common.gb')}`
}

export function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString(i18n.global.locale.value === 'ru' ? 'ru-RU' : 'en-GB', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

export function fmtUptime(s) {
  if (!s) return '—'
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return d ? `${d}d ${h}h ${m}m` : h ? `${h}h ${m}m` : `${m}m`
}

export function stateType(state) {
  return { Running: 'success', Off: 'info', Saved: 'warning', Paused: 'warning', Up: 'success', Online: 'success',
    Down: 'danger', Removed: 'danger', Unknown: 'info' }[state] || 'info'
}

export function stateText(state) {
  const key = `states.${state}`
  return i18n.global.te(key) ? t(key) : state
}

export function statusType(status) {
  return { ready: 'success', creating: 'primary', deleting: 'warning', error: 'danger', ok: 'success',
    warning: 'warning', unknown: 'info', success: 'success', running: 'primary', queued: 'info',
    uploading: 'primary' }[status] || 'info'
}

export function pct(used, limit) {
  if (limit === null || limit === undefined) return 0
  if (!limit) return used > 0 ? 100 : 0
  return Math.min(100, Math.round((used / limit) * 100))
}

export function taskKind(kind) {
  const key = 'taskKinds.' + String(kind || '').replace(/\./g, '_')
  return i18n.global.te(key) ? t(key) : kind
}
