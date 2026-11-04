import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { errorText, i18n } from '../i18n'
import { useTasks } from '../stores/tasks'

export function useVmActions(onChange) {
  const tasks = useTasks()
  const t = (...a) => i18n.global.t(...a)

  async function run(promise, vm) {
    try {
      const task = await promise
      if (onChange) onChange()
      const final = await tasks.track(task)
      if (onChange) onChange(final)
      return final
    } catch (e) {
      ElMessage.error(errorText(e))
      return null
    }
  }

  async function confirm(message, type = 'warning') {
    try {
      await ElMessageBox.confirm(message, t('common.confirmTitle'), { type, confirmButtonText: t('common.ok'), cancelButtonText: t('common.cancel') })
      return true
    } catch {
      return false
    }
  }

  async function power(vm, action) {
    if (action === 'poweroff' && !(await confirm(t('power.confirmPoweroff', { name: vm.name })))) return
    if (action === 'reset' && !(await confirm(t('power.confirmReset', { name: vm.name })))) return
    return run(api.post(`/api/tenant/vms/${vm.id}/power`, { action }), vm)
  }

  async function snapshot(vm) {
    let name
    try {
      const res = await ElMessageBox.prompt(t('vms.snapshotName'), t('vms.takeSnapshot'), {
        inputValue: `${vm.name}-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')}`,
        confirmButtonText: t('common.create'), cancelButtonText: t('common.cancel'),
      })
      name = res.value
    } catch {
      return
    }
    return run(api.post(`/api/tenant/vms/${vm.id}/snapshots`, { name }), vm)
  }

  async function revert(vm, snap) {
    if (!(await confirm(t('vms.revertConfirm', { vm: vm.name, name: snap.name })))) return
    return run(api.post(`/api/tenant/vms/${vm.id}/snapshots/${snap.id}/revert`), vm)
  }

  async function deleteSnapshot(vm, snap) {
    if (!(await confirm(t('common.confirmDelete', { name: snap.name })))) return
    return run(api.del(`/api/tenant/vms/${vm.id}/snapshots/${snap.id}`), vm)
  }

  async function remove(vm) {
    if (!(await confirm(t('vms.deleteConfirm', { name: vm.name }), 'error'))) return
    return run(api.del(`/api/tenant/vms/${vm.id}`), vm)
  }

  function media(vm, isoId) {
    return run(api.post(`/api/tenant/vms/${vm.id}/media`, { iso_id: isoId || null }), vm)
  }

  function refresh(vm) {
    return run(api.post(`/api/tenant/vms/${vm.id}/refresh`), vm)
  }

  return { power, snapshot, revert, deleteSnapshot, remove, media, refresh, run }
}
