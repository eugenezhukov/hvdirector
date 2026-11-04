<template>
  <div class="page">
    <div v-if="!loaded" v-loading="true" style="height: 200px" />

    <!-- ===================== no router yet ===================== -->
    <template v-else-if="!info.router">
      <div class="page-head"><h1>{{ t('edge.title') }}</h1></div>
      <div class="card empty">
        <h3>{{ t('edge.none') }}</h3>
        <p class="muted">{{ t('edge.noneHint') }}</p>
        <div class="kv">
          <div>{{ t('edge.wanIps') }}</div>
          <div>
            <template v-if="info.wan">
              <span class="mono">{{ info.wan.ips.map((i) => i.address).join(', ') }}</span>
              <span class="muted"> · VLAN {{ info.wan.vlan_id }} · {{ info.wan.cidr }} · gw {{ info.wan.gateway || '—' }}</span>
            </template>
            <el-tag v-else type="warning" size="small">{{ t('edge.noWan') }}</el-tag>
          </div>
          <div>{{ t('edge.mgmt') }}</div>
          <div><el-tag :type="info.mgmt_ready ? 'success' : 'danger'" size="small">{{ info.mgmt_ready ? t('common.yes') : t('edge.mgmtMissing') }}</el-tag></div>
          <div>{{ t('edge.iso') }}</div>
          <div><el-tag :type="info.iso_ready ? 'success' : 'danger'" size="small">{{ info.iso_ready ? t('common.yes') : t('edge.isoMissing') }}</el-tag></div>
          <div>{{ t('edge.resources') }}</div>
          <div>{{ info.sizing.vcpu }} vCPU · {{ fmtMb(info.sizing.memory_mb) }} · {{ fmtGb(info.sizing.disk_gb) }}</div>
        </div>
        <el-button v-if="canManage" type="primary" class="mt" :disabled="!info.mgmt_ready || !info.iso_ready" @click="openCreate">
          <el-icon><IconPlus /></el-icon>{{ t('edge.create') }}
        </el-button>
      </div>
    </template>

    <!-- ===================== router ===================== -->
    <template v-else>
      <div class="page-head">
        <div style="flex: 1">
          <h1 class="title">
            {{ info.router.name }}
            <el-tag :type="statusTag(info.router.status)" size="small">
              <el-icon v-if="busy" class="spin"><IconLoading /></el-icon>{{ t('edge.status.' + info.router.status) }}
            </el-tag>
          </h1>
          <div class="sub">
            <template v-if="info.router.status === 'ready' && info.router.applied_version >= info.router.config_version">
              <el-icon color="#2e9e5b"><IconCircleCheck /></el-icon> {{ t('edge.applied', { v: info.router.applied_version }) }}
            </template>
            <template v-else-if="info.router.status === 'ready'"><el-icon class="spin"><IconLoading /></el-icon> {{ t('edge.pending') }}</template>
            <template v-else-if="info.router.status === 'deploying'">{{ t('edge.deployHint') }}</template>
          </div>
        </div>
        <template v-if="canManage">
          <el-button :disabled="info.router.status !== 'ready' && info.router.status !== 'error'" @click="reapply">
            <el-icon><IconRefresh /></el-icon>{{ t('edge.reapply') }}
          </el-button>
          <el-button type="danger" plain :disabled="info.router.status === 'deleting'" @click="removeRouter">
            <el-icon><IconDelete /></el-icon>{{ t('common.delete') }}
          </el-button>
        </template>
      </div>
      <el-alert v-if="info.router.status === 'error' && info.router.status_message" :title="info.router.status_message" type="error" show-icon :closable="false" class="mb" />
      <el-alert v-else-if="info.router.last_error" :title="`${t('edge.notApplied')}: ${info.router.last_error}`" type="warning" show-icon :closable="false" class="mb" />

      <el-tabs v-model="tab" class="card">
        <!-- overview -->
        <el-tab-pane :label="t('edge.overview')" name="overview">
          <div class="grid-2">
            <div>
              <h4>{{ t('edge.wan') }}</h4>
              <div v-if="info.router.wan" class="kv">
                <div>{{ t('edge.wanNetwork') }}</div><div>{{ info.router.wan.network }} · VLAN {{ info.router.wan.vlan_id }}</div>
                <div>{{ t('networks.cidr') }}</div><div class="mono">{{ info.router.wan.cidr }}</div>
                <div>{{ t('networks.gateway') }}</div><div class="mono">{{ info.router.wan.gateway || '—' }}</div>
                <div>{{ t('edge.wanIps') }}</div><div class="mono">{{ info.router.wan.ips.map((i) => i.address).join(', ') }}</div>
              </div>
              <el-tag v-else type="warning" size="small">{{ t('edge.noWan') }}</el-tag>
              <h4>{{ t('edge.resources') }}</h4>
              <div v-if="info.router.vm" class="kv">
                <div>VM</div><div>{{ stateText(info.router.vm.power_state) }}</div>
                <div>vCPU / RAM / {{ t('common.size') }}</div>
                <div>{{ info.router.vm.vcpu }} / {{ fmtMb(info.router.vm.memory_mb) }} / {{ fmtGb(info.router.vm.disk_gb) }} ({{ info.router.storage_class }})</div>
              </div>
            </div>
            <div>
              <h4>{{ t('edge.networks') }}</h4>
              <div class="form-hint mb">{{ t('edge.networksHint') }}</div>
              <el-checkbox-group v-model="edit.network_ids" :disabled="!canManage" class="nets">
                <el-checkbox v-for="n in info.networks" :key="n.id" :value="n.id" :disabled="!n.eligible">
                  {{ n.name }} · VLAN {{ n.vlan_id }}
                  <span class="muted mono">{{ n.eligible ? `${n.gateway} (${n.cidr})` : t('edge.networkNotEligible') }}</span>
                </el-checkbox>
              </el-checkbox-group>
              <h4>{{ t('edge.settings') }}</h4>
              <el-checkbox v-model="edit.firewall_stateful" :disabled="!canManage">{{ t('edge.stateful') }}</el-checkbox>
              <div class="form-hint">{{ t('edge.statefulHint') }}</div>
              <div class="row mt">
                <span>{{ t('edge.defaultAction') }}:</span>
                <el-radio-group v-model="edit.firewall_default_action" :disabled="!canManage" size="small">
                  <el-radio-button value="drop">{{ t('edge.deny') }}</el-radio-button>
                  <el-radio-button value="accept">{{ t('edge.allow') }}</el-radio-button>
                </el-radio-group>
              </div>
              <el-checkbox v-model="edit.firewall_log_default" :disabled="!canManage" class="mt">{{ t('edge.logDefault') }}</el-checkbox>
              <div v-if="canManage" class="mt"><el-button type="primary" :disabled="!overviewDirty" @click="saveOverview">{{ t('common.save') }}</el-button></div>
            </div>
          </div>
        </el-tab-pane>

        <!-- firewall -->
        <el-tab-pane :label="`${t('edge.firewall')} (${rules.length})`" name="fw">
          <div class="toolbar">
            <el-tag :type="info.router.firewall_stateful ? 'success' : 'warning'">{{ info.router.firewall_stateful ? 'stateful' : 'stateless' }}</el-tag>
            <span class="muted">{{ t('edge.defaultAction') }}: <b>{{ info.router.firewall_default_action === 'drop' ? t('edge.deny') : t('edge.allow') }}</b></span>
            <span class="spacer" />
            <el-button v-if="canManage" type="primary" size="small" @click="openRule()"><el-icon><IconPlus /></el-icon>{{ t('edge.rule.add') }}</el-button>
          </div>
          <div class="form-hint mb">{{ t('edge.rule.dnatHint') }}</div>
          <el-table :data="rules" size="small" :empty-text="t('common.noData')">
            <el-table-column label="#" width="50"><template #default="{ $index }">{{ $index + 1 }}</template></el-table-column>
            <el-table-column :label="t('edge.rule.name')" min-width="150">
              <template #default="{ row }"><span :class="{ muted: !row.enabled }">{{ row.name }}</span></template>
            </el-table-column>
            <el-table-column :label="t('edge.rule.sources')" min-width="170"><template #default="{ row }">{{ items(row.sources) }}</template></el-table-column>
            <el-table-column :label="t('edge.rule.destinations')" min-width="170"><template #default="{ row }">{{ items(row.destinations) }}</template></el-table-column>
            <el-table-column :label="t('edge.rule.protocol')" min-width="150"><template #default="{ row }"><span class="mono">{{ proto(row) }}</span></template></el-table-column>
            <el-table-column :label="t('edge.rule.action')" width="120">
              <template #default="{ row }"><el-tag size="small" :type="actionTag(row.action)">{{ t('edge.rule.' + row.action) }}</el-tag></template>
            </el-table-column>
            <el-table-column :label="t('edge.rule.enabled')" width="90">
              <template #default="{ row }"><el-switch :model-value="row.enabled" size="small" :disabled="!canManage" @change="(v) => toggleRule(row, v)" /></template>
            </el-table-column>
            <el-table-column v-if="canManage" width="160" align="right">
              <template #default="{ row, $index }">
                <el-button link :disabled="$index === 0" @click="move(rules, 'firewall', $index, -1)"><el-icon><IconTop /></el-icon></el-button>
                <el-button link :disabled="$index === rules.length - 1" @click="move(rules, 'firewall', $index, 1)"><el-icon><IconBottom /></el-icon></el-button>
                <el-button link @click="openRule(row)"><el-icon><IconEdit /></el-icon></el-button>
                <el-button link type="danger" @click="deleteItem('firewall', row)"><el-icon><IconDelete /></el-icon></el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- NAT -->
        <el-tab-pane :label="`${t('edge.natTab')} (${nat.length})`" name="nat">
          <div class="toolbar">
            <span class="spacer" />
            <el-button v-if="canManage" type="primary" size="small" :disabled="!extIps.length" @click="openNat()"><el-icon><IconPlus /></el-icon>{{ t('edge.nat.add') }}</el-button>
          </div>
          <el-table :data="nat" size="small" :empty-text="t('common.noData')">
            <el-table-column label="#" width="50"><template #default="{ $index }">{{ $index + 1 }}</template></el-table-column>
            <el-table-column :label="t('edge.rule.name')" min-width="140">
              <template #default="{ row }"><span :class="{ muted: !row.enabled }">{{ row.name }}</span></template>
            </el-table-column>
            <el-table-column :label="t('edge.nat.kind')" width="90"><template #default="{ row }"><el-tag size="small" :type="row.kind === 'dnat' ? 'warning' : 'primary'">{{ row.kind.toUpperCase() }}</el-tag></template></el-table-column>
            <el-table-column :label="t('edge.nat.externalIp')" min-width="170">
              <template #default="{ row }"><span class="mono">{{ row.external_ip }}{{ row.kind === 'dnat' && row.external_port ? ':' + row.external_port : '' }}</span></template>
            </el-table-column>
            <el-table-column :label="t('edge.nat.internal')" min-width="170">
              <template #default="{ row }"><span class="mono">{{ row.internal_address }}{{ row.internal_port ? ':' + row.internal_port : '' }}</span></template>
            </el-table-column>
            <el-table-column :label="t('edge.rule.protocol')" width="100"><template #default="{ row }">{{ t('edge.proto.' + row.protocol) }}</template></el-table-column>
            <el-table-column :label="t('edge.nat.match')" min-width="130"><template #default="{ row }"><span class="mono">{{ row.match_address || '—' }}</span></template></el-table-column>
            <el-table-column :label="t('edge.rule.enabled')" width="90">
              <template #default="{ row }"><el-switch :model-value="row.enabled" size="small" :disabled="!canManage" @change="(v) => toggleNat(row, v)" /></template>
            </el-table-column>
            <el-table-column v-if="canManage" width="160" align="right">
              <template #default="{ row, $index }">
                <el-button link :disabled="$index === 0" @click="move(nat, 'nat', $index, -1)"><el-icon><IconTop /></el-icon></el-button>
                <el-button link :disabled="$index === nat.length - 1" @click="move(nat, 'nat', $index, 1)"><el-icon><IconBottom /></el-icon></el-button>
                <el-button link @click="openNat(row)"><el-icon><IconEdit /></el-icon></el-button>
                <el-button link type="danger" @click="deleteItem('nat', row)"><el-icon><IconDelete /></el-icon></el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- DHCP -->
        <el-tab-pane :label="t('edge.dhcp.tab')" name="dhcp" lazy>
          <EdgeDhcp ref="dhcpRef" :can-manage="canManage" :ready="info.router.status === 'ready'" @changed="load(overviewDirty)" />
        </el-tab-pane>

        <!-- IP sets -->
        <el-tab-pane :label="`${t('edge.ipsets')} (${ipsets.length})`" name="ipsets">
          <div class="toolbar">
            <span class="spacer" />
            <el-button v-if="canManage" type="primary" size="small" @click="openSet()"><el-icon><IconPlus /></el-icon>{{ t('edge.ipset.add') }}</el-button>
          </div>
          <el-table :data="ipsets" size="small" :empty-text="t('common.noData')">
            <el-table-column prop="name" :label="t('common.name')" width="180" />
            <el-table-column :label="t('edge.ipset.entries')" min-width="260"><template #default="{ row }"><span class="mono">{{ row.entries.join(', ') }}</span></template></el-table-column>
            <el-table-column :label="t('edge.ipset.usedBy')" min-width="160"><template #default="{ row }">{{ row.used_by.join(', ') || '—' }}</template></el-table-column>
            <el-table-column v-if="canManage" width="100" align="right">
              <template #default="{ row }">
                <el-button link @click="openSet(row)"><el-icon><IconEdit /></el-icon></el-button>
                <el-button link type="danger" @click="deleteSet(row)"><el-icon><IconDelete /></el-icon></el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </template>

    <!-- create router -->
    <el-dialog v-model="createDlg" :title="t('edge.create')" width="620px">
      <el-form label-width="200px">
        <el-form-item :label="t('edge.name')"><el-input v-model="createForm.name" /></el-form-item>
        <el-form-item :label="t('edge.storageClass')">
          <el-select v-model="createForm.storage_class_id" style="width: 280px">
            <el-option v-for="c in classes" :key="c.id" :value="c.id" :label="c.name" :disabled="!c.available" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('edge.networks')">
          <el-checkbox-group v-model="createForm.network_ids" class="nets">
            <el-checkbox v-for="n in info.networks" :key="n.id" :value="n.id" :disabled="!n.eligible">
              {{ n.name }} · VLAN {{ n.vlan_id }} <span class="muted mono">{{ n.eligible ? n.cidr : t('edge.networkNotEligible') }}</span>
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item :label="t('edge.stateful')"><el-switch v-model="createForm.firewall_stateful" /></el-form-item>
        <el-form-item :label="t('edge.defaultAction')">
          <el-radio-group v-model="createForm.firewall_default_action">
            <el-radio-button value="drop">{{ t('edge.deny') }}</el-radio-button>
            <el-radio-button value="accept">{{ t('edge.allow') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-alert :title="`${t('edge.resources')}: ${info?.sizing.vcpu} vCPU · ${fmtMb(info?.sizing.memory_mb)} · ${fmtGb(info?.sizing.disk_gb)}`" type="info" :closable="false" show-icon />
      </el-form>
      <template #footer>
        <el-button @click="createDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!createForm.storage_class_id" @click="create">{{ t('common.create') }}</el-button>
      </template>
    </el-dialog>

    <!-- firewall rule -->
    <el-dialog v-model="ruleDlg" :title="ruleForm.id ? t('common.edit') : t('edge.rule.add')" width="720px" top="5vh">
      <el-form label-width="170px">
        <el-form-item :label="t('edge.rule.name')" required><el-input v-model="ruleForm.name" maxlength="64" /></el-form-item>
        <el-form-item :label="t('edge.rule.action')">
          <el-radio-group v-model="ruleForm.action">
            <el-radio-button value="allow">{{ t('edge.rule.allow') }}</el-radio-button>
            <el-radio-button value="drop">{{ t('edge.rule.drop') }}</el-radio-button>
            <el-radio-button value="reject">{{ t('edge.rule.reject') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('edge.rule.sources')"><AddressPicker v-model="ruleForm.sources" :ipsets="ipsets" :networks="info?.networks || []" /></el-form-item>
        <el-form-item :label="t('edge.rule.destinations')"><AddressPicker v-model="ruleForm.destinations" :ipsets="ipsets" :networks="info?.networks || []" /></el-form-item>
        <el-form-item :label="t('edge.rule.protocol')">
          <el-select v-model="ruleForm.protocol" style="width: 160px">
            <el-option v-for="p in ['any', 'tcp', 'udp', 'tcp_udp', 'icmp']" :key="p" :value="p" :label="t('edge.proto.' + p)" />
          </el-select>
        </el-form-item>
        <template v-if="['tcp', 'udp', 'tcp_udp'].includes(ruleForm.protocol)">
          <el-form-item :label="t('edge.rule.dstPorts')">
            <el-input v-model="ruleForm.dst_ports" placeholder="80, 443, 8000-8100" />
            <div class="form-hint">{{ t('edge.rule.portsHint') }}</div>
          </el-form-item>
          <el-form-item :label="t('edge.rule.srcPorts')"><el-input v-model="ruleForm.src_ports" :placeholder="t('edge.rule.any')" /></el-form-item>
        </template>
        <el-form-item :label="t('edge.rule.log')"><el-switch v-model="ruleForm.log" /></el-form-item>
        <el-form-item :label="t('edge.rule.enabled')"><el-switch v-model="ruleForm.enabled" /></el-form-item>
        <el-form-item :label="t('common.description')"><el-input v-model="ruleForm.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveRule">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- NAT rule -->
    <el-dialog v-model="natDlg" :title="natForm.id ? t('common.edit') : t('edge.nat.add')" width="640px">
      <el-form label-width="220px">
        <el-form-item :label="t('edge.rule.name')" required><el-input v-model="natForm.name" maxlength="64" /></el-form-item>
        <el-form-item :label="t('edge.nat.kind')">
          <el-radio-group v-model="natForm.kind">
            <el-radio-button value="snat">{{ t('edge.nat.snat') }}</el-radio-button>
            <el-radio-button value="dnat">{{ t('edge.nat.dnat') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('edge.nat.externalIp')">
          <el-select v-model="natForm.external_ip_id" style="width: 220px">
            <el-option v-for="i in extIps" :key="i.id" :value="i.id" :label="i.address" />
          </el-select>
        </el-form-item>
        <el-form-item :label="natForm.kind === 'snat' ? t('edge.nat.internalSnat') : t('edge.nat.internalDnat')" required>
          <el-input v-model="natForm.internal_address" :placeholder="natForm.kind === 'snat' ? '10.0.0.0/24' : '10.0.0.10'" />
        </el-form-item>
        <el-form-item :label="t('edge.rule.protocol')">
          <el-select v-model="natForm.protocol" style="width: 160px">
            <el-option v-for="p in ['any', 'tcp', 'udp', 'tcp_udp']" :key="p" :value="p" :label="t('edge.proto.' + p)" />
          </el-select>
        </el-form-item>
        <template v-if="natForm.kind === 'dnat' && natForm.protocol !== 'any'">
          <el-form-item :label="t('edge.nat.externalPort')"><el-input v-model="natForm.external_port" :placeholder="t('edge.nat.allPorts')" style="width: 200px" /></el-form-item>
          <el-form-item :label="t('edge.nat.internalPort')"><el-input v-model="natForm.internal_port" placeholder="= external" style="width: 200px" /></el-form-item>
        </template>
        <el-form-item :label="natForm.kind === 'snat' ? t('edge.nat.matchSnat') : t('edge.nat.matchDnat')">
          <el-input v-model="natForm.match_address" placeholder="192.0.2.0/24" />
        </el-form-item>
        <el-form-item :label="t('edge.rule.log')"><el-switch v-model="natForm.log" /></el-form-item>
        <el-form-item :label="t('edge.rule.enabled')"><el-switch v-model="natForm.enabled" /></el-form-item>
        <el-form-item :label="t('common.description')"><el-input v-model="natForm.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="natDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveNat">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- IP set -->
    <el-dialog v-model="setDlg" :title="setForm.id ? t('common.edit') : t('edge.ipset.add')" width="520px">
      <el-form label-width="140px">
        <el-form-item :label="t('common.name')" required><el-input v-model="setForm.name" maxlength="64" /></el-form-item>
        <el-form-item :label="t('edge.ipset.entries')">
          <el-input v-model="setForm.text" type="textarea" :rows="8" placeholder="192.0.2.0/24&#10;198.51.100.7&#10;203.0.113.10-203.0.113.20" />
          <div class="form-hint">{{ t('edge.ipset.entriesHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('common.description')"><el-input v-model="setForm.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="setDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveSet">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { errorText } from '../../i18n'
import { useAuth } from '../../stores/auth'
import { useTasks } from '../../stores/tasks'
import AddressPicker from '../../components/AddressPicker.vue'
import EdgeDhcp from '../../components/EdgeDhcp.vue'
import { fmtGb, fmtMb, stateText } from '../../utils/format'

const { t } = useI18n()
const auth = useAuth()
const tasks = useTasks()
const loaded = ref(false)
const info = ref(null)
const rules = ref([])
const nat = ref([])
const ipsets = ref([])
const extIps = ref([])
const classes = ref([])
const tab = ref('overview')
const dhcpRef = ref(null)
// connected networks may have changed on the overview tab
watch(tab, (v) => { if (v === 'dhcp') dhcpRef.value?.load() })
const edit = reactive({ network_ids: [], firewall_stateful: true, firewall_default_action: 'drop', firewall_log_default: false })
const canManage = computed(() => auth.can('router.manage'))
const busy = computed(() => {
  const r = info.value?.router
  return !!r && (r.status === 'deploying' || r.status === 'deleting' || (r.status === 'ready' && r.applied_version < r.config_version))
})
const overviewDirty = computed(() => {
  const r = info.value?.router
  if (!r) return false
  const nets = r.networks.map((n) => n.id).sort().join()
  return nets !== [...edit.network_ids].sort().join() || r.firewall_stateful !== edit.firewall_stateful ||
    r.firewall_default_action !== edit.firewall_default_action || r.firewall_log_default !== edit.firewall_log_default
})
let timer = null

function syncEdit() {
  const r = info.value?.router
  if (!r) return
  Object.assign(edit, { network_ids: r.networks.map((n) => n.id), firewall_stateful: r.firewall_stateful,
    firewall_default_action: r.firewall_default_action, firewall_log_default: r.firewall_log_default })
}

async function load(keepEdit = false) {
  try {
    info.value = await api.get('/api/tenant/router')
    if (info.value.router) {
      ;[rules.value, nat.value, ipsets.value, extIps.value] = await Promise.all([
        api.get('/api/tenant/router/firewall'), api.get('/api/tenant/router/nat'), api.get('/api/tenant/ip-sets'),
        api.get('/api/tenant/external-ips'),
      ])
      if (!keepEdit) syncEdit()
    }
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    loaded.value = true
  }
}

function poll() {
  timer = setTimeout(async () => {
    if (busy.value) await load(overviewDirty.value)
    poll()
  }, 4000)
}

const statusTag = (s) => ({ ready: 'success', deploying: 'primary', error: 'danger', deleting: 'warning' })[s] || 'info'
const actionTag = (a) => ({ allow: 'success', drop: 'danger', reject: 'warning' })[a]

function items(list) {
  if (!list || !list.length) return t('edge.rule.any')
  return list.map((it) => {
    if (it.type === 'ipset') return '@' + (ipsets.value.find((s) => s.id === it.value)?.name || '?')
    if (it.type === 'network') return '#' + (info.value.networks.find((n) => n.id === it.value)?.name || '?')
    return it.value
  }).join(', ')
}

function proto(r) {
  const name = t('edge.proto.' + r.protocol)
  const ports = []
  if (r.src_ports) ports.push(`src ${r.src_ports}`)
  if (r.dst_ports) ports.push(r.dst_ports)
  return ports.length ? `${name} ${ports.join(' → ')}` : name
}

async function act(promise, notify = false) {
  try {
    const res = await promise
    const isTask = (x) => !!x && typeof x === 'object' && 'progress' in x && 'kind' in x && 'status' in x
    const task = res && res.task !== undefined ? res.task : isTask(res) ? res : null
    await load()
    if (task && task.id) tasks.track(task, { notify }).then(() => load())
    return res
  } catch (e) {
    ElMessage.error(errorText(e))
    throw e
  }
}

// ---- router lifecycle
const createDlg = ref(false)
const createForm = reactive({})
async function openCreate() {
  classes.value = await api.get('/api/tenant/storage-classes')
  Object.assign(createForm, { name: 'edge', storage_class_id: classes.value.find((c) => c.available)?.id || null,
    network_ids: info.value.networks.filter((n) => n.eligible).map((n) => n.id), firewall_stateful: true,
    firewall_default_action: 'drop' })
  createDlg.value = true
}
async function create() {
  try {
    await act(api.post('/api/tenant/router', { ...createForm }), true)
    createDlg.value = false
  } catch { /* shown */ }
}
const saveOverview = () => act(api.patch('/api/tenant/router', { ...edit })).catch(() => {})
const reapply = () => act(api.post('/api/tenant/router/apply'), true).catch(() => {})
async function removeRouter() {
  try {
    await ElMessageBox.confirm(t('edge.deleteConfirm', { name: info.value.router.name }), t('common.confirmTitle'), { type: 'error', confirmButtonText: t('common.yesDelete') })
  } catch {
    return
  }
  act(api.del('/api/tenant/router'), true).catch(() => {})
}

// ---- firewall rules
const ruleDlg = ref(false)
const ruleForm = reactive({})
function openRule(r = null) {
  Object.assign(ruleForm, r ? JSON.parse(JSON.stringify(r)) : { id: null, name: '', action: 'allow', protocol: 'tcp',
    sources: [], destinations: [], src_ports: '', dst_ports: '', log: false, enabled: true, description: '' })
  ruleDlg.value = true
}
async function saveRule() {
  const body = { ...ruleForm }
  delete body.id
  delete body.position
  if (!['tcp', 'udp', 'tcp_udp'].includes(body.protocol)) body.src_ports = body.dst_ports = ''
  try {
    await act(ruleForm.id ? api.patch(`/api/tenant/router/firewall/${ruleForm.id}`, body) : api.post('/api/tenant/router/firewall', body))
    ruleDlg.value = false
  } catch { /* shown */ }
}
function toggleRule(r, enabled) {
  const body = { ...r, enabled }
  delete body.id
  delete body.position
  act(api.patch(`/api/tenant/router/firewall/${r.id}`, body)).catch(() => {})
}

// ---- NAT rules
const natDlg = ref(false)
const natForm = reactive({})
function openNat(r = null) {
  Object.assign(natForm, r ? { ...r } : { id: null, name: '', kind: 'snat', external_ip_id: extIps.value[0]?.id, internal_address: '',
    protocol: 'any', external_port: '', internal_port: '', match_address: '', log: false, enabled: true, description: '' })
  natDlg.value = true
}
function natBody(r) {
  const body = { name: r.name, kind: r.kind, external_ip_id: r.external_ip_id, internal_address: r.internal_address,
    protocol: r.protocol, external_port: r.external_port, internal_port: r.internal_port, match_address: r.match_address,
    log: r.log, enabled: r.enabled, description: r.description }
  if (body.kind === 'snat' || body.protocol === 'any') body.external_port = body.internal_port = ''
  return body
}
async function saveNat() {
  try {
    await act(natForm.id ? api.patch(`/api/tenant/router/nat/${natForm.id}`, natBody(natForm)) : api.post('/api/tenant/router/nat', natBody(natForm)))
    natDlg.value = false
  } catch { /* shown */ }
}
const toggleNat = (r, enabled) => act(api.patch(`/api/tenant/router/nat/${r.id}`, natBody({ ...r, enabled }))).catch(() => {})

// ---- ordering / deletion
function move(list, kind, index, delta) {
  const ids = list.map((x) => x.id)
  const [id] = ids.splice(index, 1)
  ids.splice(index + delta, 0, id)
  act(api.post(`/api/tenant/router/${kind}/order`, { ids })).catch(() => {})
}
async function deleteItem(kind, r) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: r.name }), t('common.confirmTitle'), { type: 'warning' })
  } catch {
    return
  }
  act(api.del(`/api/tenant/router/${kind}/${r.id}`)).catch(() => {})
}

// ---- IP sets
const setDlg = ref(false)
const setForm = reactive({})
function openSet(s = null) {
  Object.assign(setForm, s ? { id: s.id, name: s.name, description: s.description, text: s.entries.join('\n') }
    : { id: null, name: '', description: '', text: '' })
  setDlg.value = true
}
async function saveSet() {
  const body = { name: setForm.name, description: setForm.description, entries: setForm.text.split(/[\s,;]+/).filter(Boolean) }
  try {
    await act(setForm.id ? api.patch(`/api/tenant/ip-sets/${setForm.id}`, body) : api.post('/api/tenant/ip-sets', body))
    setDlg.value = false
  } catch { /* shown */ }
}
async function deleteSet(s) {
  try {
    await ElMessageBox.confirm(t('common.confirmDelete', { name: s.name }), t('common.confirmTitle'), { type: 'warning' })
  } catch {
    return
  }
  act(api.del(`/api/tenant/ip-sets/${s.id}`)).catch(() => {})
}

onMounted(async () => {
  await load()
  poll()
})
onUnmounted(() => clearTimeout(timer))
</script>

<style scoped>
.title { display: flex; align-items: center; gap: 10px; }
.empty { max-width: 860px; }
.mt { margin-top: 12px; }
.mb { margin-bottom: 10px; }
.row { display: flex; gap: 10px; align-items: center; }
.nets { display: flex; flex-direction: column; align-items: flex-start; }
h4 { margin: 14px 0 8px; font-size: 14px; }
.spin { animation: spin 1.2s linear infinite; margin-right: 4px; vertical-align: middle; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
