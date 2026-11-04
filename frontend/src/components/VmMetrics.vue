<template>
  <div class="vmm">
    <div class="vmm-bar">
      <el-radio-group v-model="range" size="small" @change="load">
        <el-radio-button v-for="r in RANGES" :key="r" :value="r">{{ t('metrics.ranges.' + r) }}</el-radio-button>
      </el-radio-group>
      <el-button size="small" :loading="loading" @click="load"><el-icon><IconRefresh /></el-icon>{{ t('common.refresh') }}</el-button>
      <span class="vmm-spacer" />
      <span v-if="data" class="vmm-muted">{{ t('metrics.interval', { s: data.interval_s }) }}</span>
      <el-radio-group v-model="view" size="small">
        <el-radio-button value="chart">{{ t('metrics.chart') }}</el-radio-button>
        <el-radio-button value="table">{{ t('metrics.table') }}</el-radio-button>
      </el-radio-group>
    </div>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="vmm-mb" />

    <el-empty v-if="data && !hasData" :description="t('metrics.noData')" :image-size="80">
      <div class="vmm-muted">{{ t('metrics.noDataHint', { s: data.interval_s }) }}</div>
    </el-empty>

    <template v-else-if="data">
      <div class="vmm-tiles" :class="{ 'vmm-stale': loading }">
        <div class="vmm-tile">
          <div class="vmm-tile-label">{{ t('metrics.cpu') }}</div>
          <div class="vmm-tile-value">{{ fmt.pct(last.cpu_pct) }}</div>
          <div class="vmm-tile-sub">{{ t('metrics.vcpu', { n: data.vcpu }) }}</div>
        </div>
        <div class="vmm-tile">
          <div class="vmm-tile-label">{{ hasDemand ? t('metrics.memDemand') : t('metrics.memAssigned') }}</div>
          <div class="vmm-tile-value">{{ fmt.mb(hasDemand ? last.mem_demand_mb : last.mem_assigned_mb) }}</div>
          <div class="vmm-tile-sub">{{ t('metrics.of', { total: fmt.mb(data.memory_mb) }) }}</div>
        </div>
        <div class="vmm-tile">
          <div class="vmm-tile-label">{{ t('metrics.diskRead') }} / {{ t('metrics.diskWrite') }}</div>
          <div class="vmm-tile-value">{{ fmt.bytes(last.disk_read_bps) }} / {{ fmt.bytes(last.disk_write_bps) }}</div>
          <div class="vmm-tile-sub">{{ fmt.count(last.disk_iops) }} IOPS</div>
        </div>
        <div class="vmm-tile">
          <div class="vmm-tile-label">{{ t('metrics.rx') }} / {{ t('metrics.tx') }}</div>
          <div class="vmm-tile-value">{{ fmt.bits(last.net_rx_bps) }} / {{ fmt.bits(last.net_tx_bps) }}</div>
          <div class="vmm-tile-sub">{{ data.latest ? t('metrics.last', { time: fmtDate(data.latest.ts) }) : '' }}</div>
        </div>
      </div>

      <div v-if="view === 'chart'" class="vmm-grid" :class="{ 'vmm-stale': loading }">
        <div class="vmm-card">
          <LineChart v-model:hover="hover" :title="t('metrics.cpu')" :t="data.t" :from="from" :to="to" :locale="locale"
                     :series="[{ key: 'cpu', label: t('metrics.cpu'), color: C.blue, values: s.cpu_pct, area: true }]"
                     :unit="units.pct" :y-max="100" />
        </div>
        <div class="vmm-card">
          <LineChart v-model:hover="hover" :title="t('metrics.memory')" :t="data.t" :from="from" :to="to" :locale="locale"
                     :series="memSeries" :unit="units.mb" :note="hasDemand ? '' : t('metrics.demandHint')" />
        </div>
        <div class="vmm-card">
          <LineChart v-model:hover="hover" :title="t('metrics.diskIo')" :t="data.t" :from="from" :to="to" :locale="locale"
                     :series="[{ key: 'r', label: t('metrics.diskRead'), color: C.blue, values: s.disk_read_bps },
                               { key: 'w', label: t('metrics.diskWrite'), color: C.orange, values: s.disk_write_bps }]"
                     :unit="units.bytes" />
        </div>
        <div class="vmm-card">
          <LineChart v-model:hover="hover" :title="t('metrics.iops')" :t="data.t" :from="from" :to="to" :locale="locale"
                     :series="[{ key: 'io', label: 'IOPS', color: C.blue, values: s.disk_iops, area: true }]"
                     :unit="units.count" :note="t('metrics.iopsHint')" />
        </div>
        <div class="vmm-card">
          <LineChart v-model:hover="hover" :title="t('metrics.network')" :t="data.t" :from="from" :to="to" :locale="locale"
                     :series="[{ key: 'rx', label: t('metrics.rx'), color: C.blue, values: s.net_rx_bps },
                               { key: 'tx', label: t('metrics.tx'), color: C.orange, values: s.net_tx_bps }]"
                     :unit="units.bits" />
        </div>
      </div>

      <el-table v-else :data="rows" size="small" max-height="520" class="vmm-table">
        <el-table-column :label="t('metrics.time')" width="140"><template #default="{ row }">{{ row.time }}</template></el-table-column>
        <el-table-column :label="t('metrics.cpu')" min-width="90" align="right"><template #default="{ row }">{{ fmt.pct(row.cpu_pct) }}</template></el-table-column>
        <el-table-column :label="t('metrics.memDemand')" min-width="110" align="right"><template #default="{ row }">{{ fmt.mb(row.mem_demand_mb) }}</template></el-table-column>
        <el-table-column :label="t('metrics.memAssigned')" min-width="100" align="right"><template #default="{ row }">{{ fmt.mb(row.mem_assigned_mb) }}</template></el-table-column>
        <el-table-column :label="t('metrics.diskRead')" min-width="100" align="right"><template #default="{ row }">{{ fmt.bytes(row.disk_read_bps) }}</template></el-table-column>
        <el-table-column :label="t('metrics.diskWrite')" min-width="100" align="right"><template #default="{ row }">{{ fmt.bytes(row.disk_write_bps) }}</template></el-table-column>
        <el-table-column label="IOPS" min-width="80" align="right"><template #default="{ row }">{{ fmt.count(row.disk_iops) }}</template></el-table-column>
        <el-table-column :label="t('metrics.rx')" min-width="100" align="right"><template #default="{ row }">{{ fmt.bits(row.net_rx_bps) }}</template></el-table-column>
        <el-table-column :label="t('metrics.tx')" min-width="100" align="right"><template #default="{ row }">{{ fmt.bits(row.net_tx_bps) }}</template></el-table-column>
      </el-table>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../api'
import { errorText } from '../i18n'
import { fmtDate } from '../utils/format'
import LineChart from './LineChart.vue'

const props = defineProps({ url: { type: String, required: true } }) // .../vms/{id}/metrics
const { t, locale } = useI18n()
const RANGES = ['1h', '6h', '24h', '7d', '30d']
// categorical slots 1-2 of the validated palette; memory "assigned" is a recessive reference line
const C = { blue: '#2a78d6', orange: '#eb6834', muted: '#898781' }
const range = ref('1h')
const view = ref('chart')
const data = ref(null)
const loading = ref(false)
const error = ref('')
const hover = ref(null)
let timer = null

async function load() {
  loading.value = true
  try {
    data.value = await api.get(`${props.url}?range=${range.value}`)
    error.value = ''
    hover.value = null
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}

const s = computed(() => data.value.series)
const from = computed(() => Date.parse(data.value.from))
const to = computed(() => Math.max(Date.parse(data.value.to), data.value.t.length ? data.value.t[data.value.t.length - 1] : 0))
const hasData = computed(() => data.value.t.length > 0)
const hasDemand = computed(() => data.value.series.mem_demand_mb.some((v) => v !== null && v > 0))
const last = computed(() => data.value.latest || {})
const memSeries = computed(() => {
  const out = []
  if (hasDemand.value) out.push({ key: 'd', label: t('metrics.memDemand'), color: C.blue, values: s.value.mem_demand_mb })
  out.push({ key: 'a', label: t('metrics.memAssigned'), color: hasDemand.value ? C.muted : C.blue, values: s.value.mem_assigned_mb,
    area: !hasDemand.value })
  return out
})

// ---- number formatting (locale-aware, auto-scaled units; ticks share one unit per axis)
const nf = (digits) => new Intl.NumberFormat(locale.value === 'en' ? 'en-GB' : 'ru-RU', { maximumFractionDigits: digits })
const num = (v) => nf(v >= 100 ? 0 : v >= 10 ? 1 : 2).format(v)
function scaled(steps) {
  const pick = (max) => steps.slice().reverse().find((st) => max >= st.div) || steps[0]
  return {
    pick,
    format: (v, info) => {
      const u = info || pick(Math.abs(v))
      return `${num(v / u.div)} ${t('metrics.units.' + u.key)}`
    },
  }
}
const units = computed(() => ({
  pct: { pick: () => ({ div: 1 }), format: (v) => `${nf(v >= 10 ? 0 : 1).format(v)} %` },
  bytes: scaled([{ div: 1, key: 'b' }, { div: 1024, key: 'kb' }, { div: 1024 ** 2, key: 'mb' }, { div: 1024 ** 3, key: 'gb' }]),
  bits: (() => {
    const b = scaled([{ div: 1, key: 'bit' }, { div: 1e3, key: 'kbit' }, { div: 1e6, key: 'mbit' }, { div: 1e9, key: 'gbit' }])
    // values are bytes/s on the wire; shown as bits/s
    return { pick: (max) => { const u = b.pick(max * 8); return { ...u, div: u.div / 8 } },
      format: (v, info) => b.format(v * 8, info ? { ...info, div: info.div * 8 } : undefined) }
  })(),
  count: { pick: (max) => (max >= 10000 ? { div: 1000, key: 'k' } : { div: 1 }),
    format: (v, info) => (info && info.key ? `${num(v / info.div)} ${t('metrics.units.k')}` : num(v)) },
  mb: { pick: (max) => (max >= 1024 ? { div: 1024, key: 'gbm' } : { div: 1, key: 'mbm' }),
    format: (v, info) => { const u = info || (Math.abs(v) >= 1024 ? { div: 1024, key: 'gbm' } : { div: 1, key: 'mbm' })
      return `${num(v / u.div)} ${t('metrics.units.' + u.key)}` } },
}))
const fmt = computed(() => {
  const u = units.value
  const wrap = (f) => (v) => (v === null || v === undefined ? '—' : f(v))
  return { pct: wrap(u.pct.format), mb: wrap((v) => u.mb.format(v)), bytes: wrap((v) => u.bytes.format(v)),
    bits: wrap((v) => u.bits.format(v)), count: wrap((v) => u.count.format(v)) }
})

const rows = computed(() => {
  const d = data.value
  const out = []
  for (let i = d.t.length - 1; i >= 0; i--) {
    if (Object.values(d.series).every((col) => col[i] === null)) continue
    const row = { time: fmtDate(d.t[i]) }
    for (const [k, col] of Object.entries(d.series)) row[k] = col[i]
    out.push(row)
  }
  return out
})

onMounted(async () => {
  await load()
  const every = Math.max(30, data.value?.interval_s || 60) * 1000
  timer = setInterval(() => { if (!document.hidden) load() }, every)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.vmm-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.vmm-spacer { flex: 1; }
.vmm-muted { color: #898781; font-size: 12px; }
.vmm-mb { margin-bottom: 10px; }
.vmm-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin-bottom: 12px; }
.vmm-tile { border: 1px solid var(--hvd-border, #dfe3e8); border-radius: 8px; padding: 10px 12px; background: #fff; }
.vmm-tile-label { font-size: 12px; color: #52514e; }
.vmm-tile-value { font-size: 20px; font-weight: 600; color: #0b0b0b; margin-top: 2px; white-space: nowrap; }
.vmm-tile-sub { font-size: 12px; color: #898781; margin-top: 2px; }
.vmm-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 12px; }
.vmm-card { border: 1px solid var(--hvd-border, #dfe3e8); border-radius: 8px; padding: 10px 12px 6px; background: #fff; min-width: 0; }
.vmm-stale { opacity: 0.6; transition: opacity 0.2s; }
@media (max-width: 720px) { .vmm-grid { grid-template-columns: 1fr; } }
</style>
