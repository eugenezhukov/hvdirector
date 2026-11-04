import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from './stores/auth'
import { setUnauthorizedHandler } from './api'

const AppShell = () => import('./layouts/AppShell.vue')

const routes = [
  { path: '/', name: 'root', component: { render: () => null } },
  { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
  { path: '/tenant/:org/login', name: 'tenant-login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
  {
    path: '/provider',
    component: AppShell,
    meta: { portal: 'provider' },
    children: [
      { path: '', name: 'p-dash', component: () => import('./views/provider/ProviderDashboard.vue') },
      { path: 'clusters', name: 'p-clusters', component: () => import('./views/provider/ClustersView.vue') },
      { path: 'clusters/:id', name: 'p-cluster', component: () => import('./views/provider/ClusterDetail.vue') },
      { path: 'storage', name: 'p-storage', component: () => import('./views/provider/StorageView.vue') },
      { path: 'external-networks', name: 'p-extnets', component: () => import('./views/provider/ExternalNetworksView.vue') },
      { path: 'routers', name: 'p-routers', component: () => import('./views/provider/ProviderRoutersView.vue') },
      { path: 'tenants', name: 'p-tenants', component: () => import('./views/provider/TenantsView.vue') },
      { path: 'tenants/:id', name: 'p-tenant', component: () => import('./views/provider/TenantDetail.vue') },
      { path: 'vms', name: 'p-vms', component: () => import('./views/provider/ProviderVms.vue') },
      { path: 'vms/:id', name: 'p-vm', component: () => import('./views/provider/ProviderVmDetail.vue') },
      { path: 'isos', name: 'p-isos', component: () => import('./views/provider/ProviderIsos.vue') },
      { path: 'users', name: 'p-users', component: () => import('./views/provider/ProviderUsers.vue') },
      { path: 'settings', name: 'p-settings', component: () => import('./views/provider/SettingsView.vue') },
      { path: 'tasks', name: 'p-tasks', component: () => import('./views/TasksView.vue') },
      { path: 'events', name: 'p-events', component: () => import('./views/EventsView.vue') },
      { path: 'profile', name: 'p-profile', component: () => import('./views/ProfileView.vue') },
    ],
  },
  {
    path: '/tenant/:org',
    component: AppShell,
    meta: { portal: 'tenant' },
    children: [
      { path: '', name: 't-dash', component: () => import('./views/tenant/TenantDashboard.vue') },
      { path: 'vms', name: 't-vms', component: () => import('./views/tenant/VmsView.vue') },
      { path: 'vms/:id', name: 't-vm', component: () => import('./views/tenant/VmDetail.vue') },
      { path: 'edge', name: 't-edge', component: () => import('./views/tenant/EdgeView.vue'), meta: { perm: 'router.view' } },
      { path: 'networks', name: 't-networks', component: () => import('./views/tenant/NetworksView.vue'), meta: { perm: 'network.view' } },
      { path: 'isos', name: 't-isos', component: () => import('./views/tenant/IsosView.vue'), meta: { perm: 'iso.view' } },
      { path: 'users', name: 't-users', component: () => import('./views/tenant/UsersView.vue'), meta: { perm: 'user.view' } },
      { path: 'tasks', name: 't-tasks', component: () => import('./views/TasksView.vue') },
      { path: 'events', name: 't-events', component: () => import('./views/EventsView.vue'), meta: { perm: 'event.view' } },
      { path: 'profile', name: 't-profile', component: () => import('./views/ProfileView.vue') },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({ history: createWebHistory(), routes })

setUnauthorizedHandler(() => {
  const auth = useAuth()
  const org = auth.me && auth.me.tenant ? auth.me.tenant.slug : null
  auth.apply(null)
  const cur = router.currentRoute.value
  if (!cur.meta.public) router.push(org ? `/tenant/${org}/login` : '/login')
})

router.beforeEach(async (to) => {
  const auth = useAuth()
  if (!auth.loaded) await auth.load()
  if (to.meta.public) {
    if (auth.me) return auth.home
    return true
  }
  if (!auth.me) {
    if (to.params.org) return `/tenant/${to.params.org}/login`
    return '/login'
  }
  if (to.name === 'root') return auth.home
  const portal = to.matched.find((r) => r.meta.portal)?.meta.portal
  if (portal === 'provider' && !auth.isProvider) return auth.home
  if (portal === 'tenant') {
    if (auth.isProvider || to.params.org !== auth.me.tenant.slug) return auth.home
    if (to.meta.perm && !auth.can(to.meta.perm)) return `/tenant/${auth.me.tenant.slug}`
  }
  return true
})
