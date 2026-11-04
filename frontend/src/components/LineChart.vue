<template>
  <figure class="lc" :aria-label="title">
    <figcaption class="lc-head">
      <span class="lc-title">{{ title }}</span>
      <span v-if="series.length === 1" class="lc-now">{{ fmtValue(valueAt(series[0])) }}</span>
    </figcaption>
    <div v-if="series.length > 1" class="lc-legend">
      <span v-for="s in series" :key="s.key" class="lc-legend-item">
        <i class="lc-key" :style="{ background: s.color }" />
        <span class="lc-legend-label">{{ s.label }}</span>
        <b class="lc-legend-value">{{ fmtValue(valueAt(s)) }}</b>
      </span>
    </div>
    <div ref="box" class="lc-box" :style="{ height: height + 'px' }">
      <svg v-if="width > 0" :width="width" :height="height" role="img" :aria-label="title" class="lc-svg">
        <!-- grid + y axis -->
        <g v-for="tk in yTicks" :key="'y' + tk">
          <line :x1="M.left" :x2="width - M.right" :y1="y(tk)" :y2="y(tk)" class="lc-grid" :class="{ 'lc-base': tk === 0 }" />
          <text :x="M.left - 6" :y="y(tk)" class="lc-tick" text-anchor="end" dominant-baseline="middle">{{ fmtTick(tk) }}</text>
        </g>
        <text v-for="tk in xTicks" :key="'x' + tk" :x="x(tk)" :y="height - 4" class="lc-tick" text-anchor="middle">{{ fmtTime(tk, true) }}</text>
        <!-- data -->
        <path v-for="s in areaSeries" :key="'a' + s.key" :d="areaPath(s)" :fill="s.color" class="lc-area" />
        <path v-for="s in series" :key="'l' + s.key" :d="linePath(s)" :stroke="s.color" class="lc-line" />
        <circle v-for="p in isolated" :key="p.k" :cx="p.cx" :cy="p.cy" r="3" :fill="p.color" />
        <!-- hover -->
        <template v-if="hoverIdx !== null">
          <line :x1="x(t[hoverIdx])" :x2="x(t[hoverIdx])" :y1="M.top" :y2="height - M.bottom" class="lc-cross" />
          <template v-for="s in series" :key="'m' + s.key">
            <circle v-if="s.values[hoverIdx] !== null && s.values[hoverIdx] !== undefined"
                    :cx="x(t[hoverIdx])" :cy="y(s.values[hoverIdx])" r="4" :fill="s.color" class="lc-marker" />
          </template>
        </template>
        <rect :x="M.left" :y="M.top" :width="plotW" :height="plotH" fill="transparent" class="lc-hit"
              tabindex="0" @pointermove="onMove" @pointerleave="onLeave" @focus="onFocus" @blur="onLeave" @keydown="onKey" />
      </svg>
      <div v-if="tip" class="lc-tip" :style="{ left: tip.left + 'px', top: M.top + 'px' }">
        <div class="lc-tip-time">{{ fmtTime(t[hoverIdx], false) }}</div>
        <div v-for="s in series" :key="'t' + s.key" class="lc-tip-row">
          <i class="lc-key" :style="{ background: s.color }" />
          <b>{{ fmtValue(s.values[hoverIdx]) }}</b>
          <span v-if="series.length > 1" class="lc-tip-label">{{ s.label }}</span>
        </div>
      </div>
    </div>
    <div v-if="note" class="lc-note">{{ note }}</div>
  </figure>
</template>

<script setup>
// Time-series line chart (SVG): 2px lines, a 10% area wash for single-series charts, hairline grid,
// crosshair + one tooltip listing every series, shared hover index between charts (v-model:hover),
// keyboard: focus the plot, arrows move along the data, Escape leaves.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  title: { type: String, required: true },
  t: { type: Array, required: true }, // ms timestamps
  from: { type: Number, required: true },
  to: { type: Number, required: true },
  series: { type: Array, required: true }, // [{ key, label, color, values, area }]
  unit: { type: Object, required: true }, // { pick(max) -> { div, suffix }, format(v) -> string }
  yMax: { type: Number, default: null },
  height: { type: Number, default: 190 },
  hover: { type: Number, default: null },
  locale: { type: String, default: 'ru' },
  note: { type: String, default: '' },
})
const emit = defineEmits(['update:hover'])

const M = { left: 64, right: 12, top: 10, bottom: 22 }
const box = ref(null)
const width = ref(0)
const inside = ref(false)
let ro = null

onMounted(() => {
  ro = new ResizeObserver((entries) => { width.value = Math.floor(entries[0].contentRect.width) })
  ro.observe(box.value)
})
onBeforeUnmount(() => ro && ro.disconnect())

const plotW = computed(() => Math.max(10, width.value - M.left - M.right))
const plotH = computed(() => props.height - M.top - M.bottom)
const hoverIdx = computed(() => (props.hover !== null && props.hover < props.t.length ? props.hover : null))

function niceStep(raw) {
  if (raw <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(raw))
  const f = raw / p
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p
}
const dataMax = computed(() => {
  let m = 0
  for (const s of props.series) for (const v of s.values) if (v !== null && v !== undefined && v > m) m = v
  return m
})
const unitInfo = computed(() => props.unit.pick(props.yMax ?? dataMax.value))
const yScale = computed(() => {
  const div = unitInfo.value.div
  if (props.yMax !== null) {
    const step = niceStep(props.yMax / div / 4) * div
    return { top: props.yMax, step }
  }
  const raw = dataMax.value / div
  const step = niceStep(raw > 0 ? raw / 4 : 1) * div
  return { top: step * Math.max(1, Math.ceil(dataMax.value / step)), step }
})
const yTicks = computed(() => {
  const out = []
  for (let v = 0; v <= yScale.value.top + 1e-9; v += yScale.value.step) out.push(+v.toFixed(6))
  return out
})
const x = (ms) => M.left + ((ms - props.from) / Math.max(1, props.to - props.from)) * plotW.value
const y = (v) => M.top + plotH.value - (v / yScale.value.top) * plotH.value

const STEPS = [5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080].map((m) => m * 60000)
const xTicks = computed(() => {
  const span = props.to - props.from
  const max = Math.max(2, Math.floor(plotW.value / 90))
  const step = STEPS.find((s) => span / s <= max) || STEPS[STEPS.length - 1]
  const offset = new Date().getTimezoneOffset() * 60000 // align ticks to local time
  const first = Math.ceil((props.from - offset) / step) * step + offset
  const out = []
  for (let v = first; v <= props.to; v += step) out.push(v)
  return out
})

function segments(s) {
  const segs = []
  let cur = []
  s.values.forEach((v, i) => {
    if (v === null || v === undefined) {
      if (cur.length) segs.push(cur)
      cur = []
    } else cur.push([x(props.t[i]), y(v)])
  })
  if (cur.length) segs.push(cur)
  return segs
}
const linePath = (s) => segments(s).filter((g) => g.length > 1).map((g) => 'M' + g.map((p) => p.join(',')).join('L')).join('')
const areaSeries = computed(() => props.series.filter((s) => s.area))
function areaPath(s) {
  const base = y(0)
  return segments(s).filter((g) => g.length > 1)
    .map((g) => `M${g[0][0]},${base}L` + g.map((p) => p.join(',')).join('L') + `L${g[g.length - 1][0]},${base}Z`).join('')
}
const isolated = computed(() => {
  const out = []
  for (const s of props.series) {
    for (const g of segments(s)) if (g.length === 1) out.push({ k: s.key + g[0][0], cx: g[0][0], cy: g[0][1], color: s.color })
  }
  return out
})

function valueAt(s) {
  if (hoverIdx.value !== null) return s.values[hoverIdx.value]
  for (let i = s.values.length - 1; i >= 0; i--) if (s.values[i] !== null && s.values[i] !== undefined) return s.values[i]
  return null
}
const fmtValue = (v) => (v === null || v === undefined ? '—' : props.unit.format(v))
const fmtTick = (v) => props.unit.format(v, unitInfo.value)
function fmtTime(ms, short) {
  const d = new Date(ms)
  const loc = props.locale === 'en' ? 'en-GB' : 'ru-RU'
  const hm = d.toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit' })
  const dm = d.toLocaleDateString(loc, { day: '2-digit', month: '2-digit' })
  if (!short) return `${dm} ${hm}`
  return props.to - props.from > 86400000 ? dm : hm
}

function nearest(ms) {
  const t = props.t
  if (!t.length) return null
  let lo = 0
  let hi = t.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (t[mid] < ms) lo = mid
    else hi = mid
  }
  return Math.abs(t[lo] - ms) <= Math.abs(t[hi] - ms) ? lo : hi
}
function onMove(e) {
  const rect = box.value.getBoundingClientRect()
  const px = e.clientX - rect.left
  const ms = props.from + ((px - M.left) / plotW.value) * (props.to - props.from)
  inside.value = true
  emit('update:hover', nearest(ms))
}
function onLeave() {
  inside.value = false
  emit('update:hover', null)
}
function onFocus() {
  inside.value = true
  if (props.t.length) emit('update:hover', props.t.length - 1)
}
function onKey(e) {
  if (!props.t.length) return
  const i = hoverIdx.value ?? props.t.length - 1
  if (e.key === 'ArrowLeft') emit('update:hover', Math.max(0, i - 1))
  else if (e.key === 'ArrowRight') emit('update:hover', Math.min(props.t.length - 1, i + 1))
  else if (e.key === 'Escape') onLeave()
  else return
  e.preventDefault()
}
const tip = computed(() => {
  if (!inside.value || hoverIdx.value === null) return null
  const cx = x(props.t[hoverIdx.value])
  const w = 190
  return { left: cx + 12 + w > width.value ? Math.max(0, cx - 12 - w) : cx + 12 }
})
</script>

<style scoped>
.lc {
  --lc-surface: #ffffff;
  --lc-grid: #e1e0d9;
  --lc-axis: #c3c2b7;
  --lc-muted: #898781;
  --lc-ink: #0b0b0b;
  --lc-ink-2: #52514e;
  margin: 0;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
}
.lc-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
.lc-title { font-size: 13px; font-weight: 600; color: var(--lc-ink); }
.lc-now { font-size: 13px; font-weight: 600; color: var(--lc-ink); }
.lc-legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 12px; color: var(--lc-ink-2); margin-bottom: 4px; }
.lc-legend-item { display: inline-flex; align-items: center; gap: 6px; }
.lc-legend-value { color: var(--lc-ink); font-weight: 600; }
.lc-key { display: inline-block; width: 12px; height: 2px; border-radius: 1px; }
.lc-box { position: relative; width: 100%; }
.lc-svg { display: block; overflow: visible; }
.lc-grid { stroke: var(--lc-grid); stroke-width: 1; shape-rendering: crispEdges; }
.lc-base { stroke: var(--lc-axis); }
.lc-tick { font-size: 11px; fill: var(--lc-muted); font-variant-numeric: tabular-nums; }
.lc-line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.lc-area { opacity: 0.1; }
.lc-cross { stroke: var(--lc-muted); stroke-width: 1; shape-rendering: crispEdges; }
.lc-marker { stroke: var(--lc-surface); stroke-width: 2; }
.lc-hit { cursor: crosshair; outline: none; }
.lc-hit:focus-visible { stroke: var(--lc-axis); stroke-width: 1; }
.lc-tip {
  position: absolute; width: 190px; pointer-events: none; background: var(--lc-surface); border: 1px solid rgba(11, 11, 11, 0.1);
  border-radius: 6px; box-shadow: 0 4px 14px rgba(11, 11, 11, 0.08); padding: 6px 8px; font-size: 12px; z-index: 2;
}
.lc-tip-time { color: var(--lc-ink-2); margin-bottom: 4px; }
.lc-tip-row { display: flex; align-items: center; gap: 6px; line-height: 18px; }
.lc-tip-row b { color: var(--lc-ink); font-weight: 600; }
.lc-tip-label { color: var(--lc-ink-2); }
.lc-note { font-size: 12px; color: var(--lc-ink-2); margin-top: 4px; }
</style>
