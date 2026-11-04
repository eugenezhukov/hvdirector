<template>
  <div class="picker">
    <div v-if="!items.length" class="muted any">{{ t('edge.rule.any') }}</div>
    <div v-for="(it, i) in items" :key="i" class="row">
      <el-select v-model="it.type" size="small" style="width: 170px" @change="it.value = ''">
        <el-option value="cidr" :label="t('edge.addr.cidr')" />
        <el-option value="ipset" :label="t('edge.addr.ipset')" :disabled="!ipsets.length" />
        <el-option value="network" :label="t('edge.addr.network')" :disabled="!networks.length" />
      </el-select>
      <el-input v-if="it.type === 'cidr'" v-model="it.value" size="small" :placeholder="t('edge.addr.placeholder')" style="flex: 1" />
      <el-select v-else-if="it.type === 'ipset'" v-model="it.value" size="small" style="flex: 1">
        <el-option v-for="s in ipsets" :key="s.id" :value="s.id" :label="`${s.name} (${s.entries.length})`" />
      </el-select>
      <el-select v-else v-model="it.value" size="small" style="flex: 1">
        <el-option v-for="n in networks" :key="n.id" :value="n.id" :label="`${n.name} — ${n.cidr || '?'}`" :disabled="!n.cidr" />
      </el-select>
      <el-button link type="danger" @click="items.splice(i, 1)"><el-icon><IconDelete /></el-icon></el-button>
    </div>
    <el-button size="small" @click="items.push({ type: 'cidr', value: '' })"><el-icon><IconPlus /></el-icon>{{ t('edge.addr.add') }}</el-button>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'

// v-model: [{ type: 'cidr'|'ipset'|'network', value }]; empty list = any
const items = defineModel({ type: Array, default: () => [] })
defineProps({ ipsets: { type: Array, default: () => [] }, networks: { type: Array, default: () => [] } })
const { t } = useI18n()
</script>

<style scoped>
.picker { width: 100%; }
.row { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.any { font-size: 13px; margin-bottom: 6px; }
</style>
