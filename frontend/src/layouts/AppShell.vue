<template>
  <div class="shell">
    <header class="hdr">
      <div class="brand" @click="router.push(auth.home)">
        <img src="/favicon.svg" alt="" />
        <div>
          <div class="brand-name">{{ t('product') }}</div>
          <div class="brand-sub">{{ portal === 'provider' ? t('nav.providerPortal') : auth.tenant?.name }}</div>
        </div>
      </div>
      <div class="spacer" />
      <el-radio-group :model-value="currentLocale" size="small" class="lang" @change="auth.saveLocale">
        <el-radio-button value="ru">RU</el-radio-button>
        <el-radio-button value="en">EN</el-radio-button>
      </el-radio-group>
      <el-dropdown trigger="click" @command="onUserCmd">
        <span class="user">
          <el-icon><IconUser /></el-icon>
          {{ auth.me?.user.full_name || auth.me?.user.username }}
          <span v-if="auth.tenant" class="org">@ {{ auth.tenant.slug }}</span>
          <el-icon><IconArrowDown /></el-icon>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="profile"><el-icon><IconSetting /></el-icon>{{ t('nav.profile') }}</el-dropdown-item>
            <el-dropdown-item command="logout" divided><el-icon><IconSwitchButton /></el-icon>{{ t('common.logout') }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </header>
    <div class="body">
      <aside class="nav">
        <el-menu :default-active="activeMenu" router class="menu">
          <template v-for="item in menu" :key="item.path || item.group">
            <div v-if="item.group" class="menu-group">{{ item.group }}</div>
            <el-menu-item v-else :index="item.path">
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.label }}</span>
            </el-menu-item>
          </template>
        </el-menu>
      </aside>
      <main class="main">
        <router-view :key="route.fullPath" />
      </main>
    </div>
    <TaskPanel />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuth } from '../stores/auth'
import { useTasks } from '../stores/tasks'
import { currentLocale } from '../i18n'
import TaskPanel from '../components/TaskPanel.vue'

const { t } = useI18n()
const auth = useAuth()
const tasks = useTasks()
const route = useRoute()
const router = useRouter()

const portal = computed(() => route.matched.find((r) => r.meta.portal)?.meta.portal)
const base = computed(() => (portal.value === 'provider' ? '/provider' : `/tenant/${auth.tenant?.slug}`))

const menu = computed(() => {
  const b = base.value
  if (portal.value === 'provider') {
    return [
      { path: b, label: t('nav.dashboard'), icon: 'IconOdometer' },
      { group: t('nav.infrastructure') },
      { path: `${b}/clusters`, label: t('nav.clusters'), icon: 'IconCpu' },
      { path: `${b}/storage`, label: t('nav.storage'), icon: 'IconCoin' },
      { path: `${b}/vms`, label: t('nav.vms'), icon: 'IconMonitor' },
      { group: t('nav.tenantPortal') },
      { path: `${b}/tenants`, label: t('nav.tenants'), icon: 'IconOfficeBuilding' },
      { path: `${b}/isos`, label: t('nav.isos'), icon: 'IconFiles' },
      { group: t('nav.administration') },
      { path: `${b}/users`, label: t('nav.users'), icon: 'IconUser' },
      { path: `${b}/tasks`, label: t('nav.tasks'), icon: 'IconList' },
      { path: `${b}/events`, label: t('nav.events'), icon: 'IconDocument' },
      { path: `${b}/settings`, label: t('nav.settings'), icon: 'IconSetting' },
    ]
  }
  const items = [
    { path: b, label: t('nav.dashboard'), icon: 'IconOdometer' },
    { path: `${b}/vms`, label: t('nav.vms'), icon: 'IconMonitor' },
  ]
  if (auth.can('network.view')) items.push({ path: `${b}/networks`, label: t('nav.networks'), icon: 'IconConnection' })
  if (auth.can('iso.view')) items.push({ path: `${b}/isos`, label: t('nav.isos'), icon: 'IconFiles' })
  if (auth.can('user.view')) items.push({ path: `${b}/users`, label: t('nav.users'), icon: 'IconUser' })
  items.push({ path: `${b}/tasks`, label: t('nav.tasks'), icon: 'IconList' })
  if (auth.can('event.view')) items.push({ path: `${b}/events`, label: t('nav.events'), icon: 'IconDocument' })
  return items
})

const activeMenu = computed(() => {
  const paths = menu.value.filter((m) => m.path).map((m) => m.path).sort((a, b) => b.length - a.length)
  return paths.find((p) => route.path === p || route.path.startsWith(p + '/')) || route.path
})

async function onUserCmd(cmd) {
  if (cmd === 'profile') router.push(`${base.value}/profile`)
  if (cmd === 'logout') {
    tasks.stop()
    const org = await auth.logout()
    router.push(org ? `/tenant/${org}/login` : '/login')
  }
}

onMounted(() => tasks.start())
onUnmounted(() => tasks.stop())
</script>

<style scoped>
.shell { display: flex; flex-direction: column; height: 100vh; }
.hdr {
  height: 52px; background: linear-gradient(90deg, var(--hvd-header), var(--hvd-header-2)); color: #fff;
  display: flex; align-items: center; padding: 0 16px; gap: 16px; flex-shrink: 0;
}
.brand { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.brand img { width: 30px; height: 30px; }
.brand-name { font-weight: 600; font-size: 16px; letter-spacing: .3px; }
.brand-sub { font-size: 12px; color: #a9c7d8; }
.spacer { flex: 1; }
.user { color: #fff; display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 14px; }
.user .org { color: #a9c7d8; }
.lang :deep(.el-radio-button__inner) { background: transparent; color: #cfe3ee; border-color: #3d6a86; }
.lang :deep(.is-active .el-radio-button__inner) { background: var(--hvd-accent); color: #0b2d42; border-color: var(--hvd-accent); }
.body { flex: 1; display: flex; min-height: 0; }
.nav { width: 230px; background: #fff; border-right: 1px solid var(--hvd-border); overflow-y: auto; flex-shrink: 0; }
.menu { border-right: none; }
.menu-group { font-size: 11px; text-transform: uppercase; color: #8a98a5; padding: 14px 20px 4px; letter-spacing: .6px; }
.menu :deep(.el-menu-item) { height: 42px; }
.main { flex: 1; overflow-y: auto; min-width: 0; padding-bottom: 44px; }
</style>
