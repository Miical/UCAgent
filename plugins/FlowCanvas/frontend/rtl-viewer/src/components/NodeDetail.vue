<template>
  <section class="node-detail" v-if="node">
    <h3 :title="node.id">
      <!-- The path text is wrapped so consumers (and the e2e suite)
           can read the instance path without picking the copy glyph
           out of the heading's text content. -->
      <span class="inst-path">
        <span v-for="(segment, i) in pathSegments" :key="i">
          <span v-if="i > 0" class="sep">.</span>{{ segment }}<wbr />
        </span>
      </span>
      <button
        type="button"
        class="copy-btn"
        :title="`复制实例路径：${node.id}`"
        aria-label="复制实例路径"
        @click="copy('path', node.id)"
      >{{ copied === 'path' ? '✓' : '📋' }}</button>
    </h3>
    <div class="nav-actions">
      <button
        type="button"
        @click="store.descend(node.id)"
        :disabled="!store.selectedHasChildren"
        :title="store.selectedHasChildren ? '仅查看此模块及子模块' : '叶模块，没有子模块'"
      >进入模块</button>
      <button
        type="button"
        @click="store.ascend()"
        :disabled="!store.rootInstancePath"
        title="返回上一级（快捷键：u）"
      >上一级</button>
      <button
        type="button"
        @click="store.goToTop()"
        :disabled="!store.rootInstancePath"
        title="返回设计顶层"
      >顶层</button>
    </div>
    <!-- Source location. Shown project-relative (store.sourceRoot, see
         sourcePaths.js) because the absolute path was four wrapped
         lines of which the leading two thirds were identical for every
         node; the absolute path is the title and the copy button's
         payload. -->
    <div v-if="hasOpenable" class="open-target-row">
      <code class="open-target" :title="absoluteTargetText || openTitle">{{ openTargetText }}</code>
      <button
        v-if="absoluteTargetText"
        type="button"
        class="copy-btn"
        :title="`复制绝对路径：${absoluteTargetText}`"
        aria-label="复制源码绝对路径"
        @click="copy('file', absoluteTargetText)"
      >{{ copied === 'file' ? '✓' : '📋' }}</button>
    </div>
    <dl>
      <dt>模块</dt><dd>{{ node.module }}</dd>
      <dt v-if="node.is_blackbox">状态</dt>
      <dd v-if="node.is_blackbox" class="blackbox">黑盒模块</dd>
      <template v-if="hasParameters">
        <dt>参数</dt>
        <dd>
          <ul>
            <li v-for="(value, key) in node.parameters" :key="key">
              <code>.{{ key }}({{ value }})</code>
            </li>
          </ul>
        </dd>
      </template>
      <template v-for="group in portGroups" :key="group.dir">
        <dt>端口 — {{ group.label }} ({{ group.ports.length }})</dt>
        <dd>
          <table class="ports-table">
            <tbody>
              <tr v-for="port in group.ports" :key="port.name">
                <td class="port-name-cell">
                  <code>{{ port.name }}</code>
                  <code
                    v-if="port.port_kind === 'interface' || port.port_kind === 'interface_signal'"
                    class="port-iface-tag"
                    :title="ifaceTagTitle(port)"
                  >{{ ifaceTag(port) }}</code>
                </td>
                <td class="port-expr-cell">
                  <code v-if="port.expr" class="port-expr">← {{ port.expr }}</code>
                </td>
              </tr>
            </tbody>
          </table>
        </dd>
      </template>
      <template v-if="coverage">
        <dt>覆盖率</dt>
        <dd class="coverage-block">
          <div v-for="row in coverageRows" :key="row.label" class="cov-row">
            <span class="cov-label">{{ row.label }}</span>
            <span class="cov-bar" :title="`${row.covered}/${row.total}`">
              <span
                class="cov-bar-fill"
                :style="{ width: row.pct + '%', background: row.color }"
              ></span>
            </span>
            <span class="cov-nums">{{ row.covered }}/{{ row.total }} ({{ row.pct }}%)</span>
          </div>
          <a
            v-if="coverage.coverview_link"
            class="coverview-link"
            :href="coverage.coverview_link"
            target="_blank"
            rel="noopener"
            title="在 Coverview 中查看模块源码"
          >在 Coverview 中打开 ↗</a>
        </dd>
      </template>
      <template v-if="liveCoverageText">
        <dt>覆盖率</dt>
        <dd class="live-cov" data-testid="node-live-coverage">
          <div class="live-cov-metrics">{{ liveCoverageText }}</div>

        </dd>
      </template>
      <template v-if="phys">
        <dt>物理信息</dt>
        <dd class="phys-block" data-testid="node-phys">
          <div class="phys-figures">
            <span
              v-for="f in physFigures"
              :key="f.label"
              class="phys-figure"
              :title="f.title"
            >
              <span class="phys-fig-label">{{ f.label }}</span>
              <span class="phys-fig-value">{{ f.value }}</span>
            </span>
          </div>
          <div v-for="bar in physBars" :key="bar.label" class="cov-row">
            <span class="cov-label">{{ bar.label }}</span>
            <span class="cov-bar" :title="bar.title">
              <span
                class="cov-bar-fill"
                :style="{ width: bar.pct + '%', background: bar.color }"
              ></span>
            </span>
            <span class="cov-nums">{{ bar.pct }}%</span>
          </div>
        </dd>
      </template>
      <template v-if="axiPins.length || axiInterconnect">
        <dt>AXI 性能</dt>
        <dd>
          <table class="axi-table" v-if="axiPins.length">
            <thead>
              <tr><th>总线</th><th>角色</th><th>吞吐量</th><th>反压</th></tr>
            </thead>
            <tbody>
              <tr v-for="p in axiPins" :key="p.port">
                <td>
                  <code>{{ p.port }}</code>
                  <span v-if="p.errors" class="axi-err" :title="p.errors + ' 个 AXI 错误响应'">⚠</span>
                </td>
                <td><span class="axi-role" :data-role="p.role">{{ p.role || '—' }}</span></td>
                <td class="axi-tput" :title="'对端：' + (p.peer || '—')">
                  <span class="rd">R {{ p.rd }}</span> <span class="wr">W {{ p.wr }}</span>
                </td>
                <td class="rb-bp" :data-level="bpLevel(p.bp)">{{ p.bp.toFixed(0) }}%</td>
              </tr>
            </tbody>
          </table>
          <div v-if="axiInterconnect" class="axi-ic">
            interconnect — read {{ fmtBps(axiInterconnect.total_read_bps) }}, write
            {{ fmtBps(axiInterconnect.total_write_bps) }}; hottest master
            <code>{{ axiInterconnect.hottest_master }}</code>
          </div>
        </dd>
      </template>
      <template v-for="(payload, name) in otherOverlays" :key="name">
        <dt>附加信息：{{ name }}</dt>
        <dd><pre>{{ JSON.stringify(payload, null, 2) }}</pre></dd>
      </template>
    </dl>
  </section>
  <section class="node-detail empty" v-else>
    <p>点击模块查看端口、参数和附加信息。</p>
  </section>
</template>

<script setup>
// Per-node detail panel. Driven entirely by the store's
// selection; click handling lives in GraphCanvas.
//
// Overlay values are rendered raw as JSON — readable, doesn't
// require this component to know about every overlay's payload
// shape, and stays useful for unknown / future overlays the
// viewer doesn't have a dedicated renderer for.
import { computed, onBeforeUnmount, ref } from 'vue'
import { useViewerStore } from '../store.js'
import { copyText } from '../clipboard.js'
import { relativeSourcePath } from '../sourcePaths.js'
import { heatColor } from '../overlays/coverage.js'
import { covSummaryText, COV_PANE_ROUTE } from '../covData.js'
import { displayOrigin } from '../displayNames.js'
import { bpLevel, heatRampColor } from '../palette.js'
import { areaOf, physScope, powerOf } from '../overlays/phys.js'
import { themeVersion } from '../theme.js'
import { formatBandwidth as fmtBps } from '../format.js'

const store = useViewerStore()
const node = computed(() => store.selectedNode)
const pathSegments = computed(() => (node.value?.id || '').split('.'))
const hasParameters = computed(
  () => node.value && node.value.parameters && Object.keys(node.value.parameters).length > 0,
)
// Coverage gets a dedicated section (progress bars + Coverview deep
// link) instead of the raw-JSON fallback the generic loop renders;
// everything without a dedicated section stays on the generic path
// so unknown / future overlays remain inspectable.
const coverage = computed(
  () => (node.value && node.value.overlays && node.value.overlays.coverage) || null,
)
const COVERAGE_CHANNELS = [
  ['lines', '行覆盖率'],
  ['branches', '分支覆盖率'],
  ['toggles', '翻转覆盖率'],
]
const coverageRows = computed(() => {
  // ``heatColor`` resolves --cov-l at call time, so the rows have to be
  // recomputed when the theme flips (the ramp moves 82% → 38%).
  themeVersion.value
  if (!coverage.value) return []
  const rows = []
  for (const [key, label] of COVERAGE_CHANNELS) {
    const ch = coverage.value[key]
    if (!ch || typeof ch.pct !== 'number') continue
    rows.push({
      label,
      covered: ch.covered,
      total: ch.total,
      pct: ch.pct,
      color: heatColor(ch.pct),
    })
  }
  return rows
})

// --- live coverage (the hub's /cov.json, joined by module name) -------
//
// Separate from the ``coverage`` block above, which is whatever the
// producer baked into view.json. This one is the hub's latest run,
// so it can be present on a payload that carries no coverage overlay
// at all — and both can show at once, which is the honest rendering:
// they are two different measurements.
//
// Rendered as one compact line rather than five progress bars. The
// bars above earn their space by being the node's own numbers; this
// is a per-MODULE roll-up whose detail lives one click away in the
// coverage pane.
const liveCoverage = computed(() => {
  const module = node.value && node.value.module
  if (typeof module !== 'string' || module.length === 0) return null
  return store.covByModule.get(module) || null
})
const liveCoverageText = computed(() => covSummaryText(liveCoverage.value))
// The pane link only exists when a hub is serving us — from embed.py
// or the dev server ``/cov`` is a 404 (or someone else's page).
const covPaneHref = computed(() => (isHubServed() ? COV_PANE_ROUTE : null))

// --- phys: area + power (rtl-buddy/rtl-buddy-sch#22) ----------------
//
// Three numbers and two bars, per the issue. The numbers are the
// node's own measurements; the bars are its share of its PARENT's
// subtree, which is the question a hierarchy panel is actually asked
// ("is this the block that owns the area?") and the one a raw µW
// figure cannot answer. The parent comes from the instance path —
// ``nodesById`` is keyed on it — so a node whose parent is outside
// the rendered subtree simply gets no bars.
//
// Only the POWER figure follows the scope toggle. The area figure is
// the producer's module roll-up in both scopes and is labelled as
// such, because a self area is not derivable here — see
// overlays/phys.js and docs/phys-overlay.md §6.
const phys = computed(
  () => (node.value && node.value.overlays && node.value.overlays.phys) || null,
)
// Which figures the panel shows follows the canvas toggle, so the
// number under the cursor is the number the node is painted with.
const physScopeLabel = computed(() => physScope({ physScope: store.physScope }))
const physParent = computed(() => {
  const id = node.value && node.value.id
  if (typeof id !== 'string') return null
  // The tree parent, not the last dot: ``top.g[0].u_s``'s parent is
  // ``top`` (rtl-buddy-sch#209).
  const parentId = store.treeParentOf.get(id)
  if (!parentId) return null
  const parent = store.nodesById.get(parentId)
  return (parent && parent.overlays && parent.overlays.phys) || null
})
const physFigures = computed(() => {
  const block = phys.value
  if (!block) return []
  const scope = physScopeLabel.value
  const out = []
  if (typeof block.cell_count === 'number') {
    out.push({
      label: '单元数',
      value: formatCount(block.cell_count),
      title:
        "模块自身的综合单元数；子模块实例按一个单元计数。",
    })
  }
  // The area label says what the figure IS, in both scopes: the
  // producer's module area, submodules included. There is no self
  // counterpart to show — the view carries no instance multiplicity,
  // so subtracting the children's areas would over-report for an
  // instance array or a generate loop. Only the power figure follows
  // the scope toggle.
  const area = areaOf(block)
  if (area !== null) {
    out.push({
      label: '模块面积（含子模块）',
      value: `${formatMetric(area)} µm²`,
      title:
        "综合报告中的模块面积，已包含子模块面积。",
    })
  }
  const power = powerOf(block, scope)
  if (power !== null) {
    out.push({
      label: scope === 'self' ? '自身功耗' : '含子模块功耗',
      value: `${formatMetric(power)} µW`,
      title:
        scope === 'self'
          ? '当前模块直接包含的叶单元总功耗。'
          : '当前模块及子模块内全部叶单元的总功耗。',
    })
  }
  return out
})
const physBars = computed(() => {
  themeVersion.value // the bar colours are resolved tokens
  const block = phys.value
  const parent = physParent.value
  if (!block || !parent) return []
  const bars = []
  const area = share(areaOf(block), areaOf(parent))
  if (area !== null) {
    bars.push({
      label: '面积',
      pct: area,
      color: heatRampColor(area / 100),
      title: '占父模块面积的比例',
    })
  }
  const power = share(powerOf(block, 'subtree'), powerOf(parent, 'subtree'))
  if (power !== null) {
    bars.push({
      label: '功耗',
      pct: power,
      color: heatRampColor(power / 100),
      title: '占父模块总功耗的比例',
    })
  }
  return bars
})
function share(value, total) {
  if (typeof value !== 'number' || typeof total !== 'number' || !(total > 0)) {
    return null
  }
  return Math.round(Math.min(100, (value / total) * 100) * 10) / 10
}
// Enough precision to keep a sub-µW leakage from reading as 0 and
// few enough digits to fit the panel — the same choice the hub's
// /phy pane makes for its columns.
function formatMetric(value) {
  const abs = Math.abs(value)
  if (abs >= 1000) return formatCount(Math.round(value))
  if (abs >= 1) return value.toFixed(2)
  if (abs === 0) return '0'
  return value.toPrecision(3)
}
function formatCount(value) {
  return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

// --- axi-perf: render the overlay human-readably instead of raw JSON.
// (throughput formatting shared via ../format.js — bytes/s, decimal MB/GB)
function axiMaxBp(block) {
  const ch = block && block.channels
  if (!ch) return 0
  let best = 0
  for (const r of ['ar', 'aw', 'r', 'w', 'b']) {
    const c = ch[r]
    if (c && typeof c.bp_pct === 'number' && c.bp_pct > best) best = c.bp_pct
  }
  return best
}
// Initiator (master) first, then target (slave), then by name —
// matching the AXI Performance tab's ordering.
const axiPins = computed(() => {
  const ov = node.value?.overlays?.['axi-perf']
  const pins = ov && Array.isArray(ov.bundle_pins) ? ov.bundle_pins : []
  const rank = (r) => (r === 'master' ? 0 : r === 'slave' ? 1 : 2)
  return pins
    .map((p) => {
      const b = p.bundle || {}
      const t = b.throughput || {}
      const e = b.errors || {}
      return {
        port: p.port,
        role: p.role,
        peer: p.peer,
        rd: fmtBps(t.read_bps),
        wr: fmtBps(t.write_bps),
        bp: axiMaxBp(b),
        errors: (e.slverr || 0) + (e.decerr || 0),
      }
    })
    .sort((a, b) => rank(a.role) - rank(b.role) || a.port.localeCompare(b.port))
})
const axiInterconnect = computed(
  () => node.value?.overlays?.['axi-perf']?.interconnect || null,
)
// Every overlay WITHOUT a dedicated section above (axi-perf,
// coverage, phys) falls back to the raw-JSON renderer.
const DEDICATED_OVERLAYS = new Set(['axi-perf', 'coverage', 'phys'])
const otherOverlays = computed(() => {
  const ov = node.value?.overlays || {}
  const out = {}
  for (const k of Object.keys(ov)) {
    if (!DEDICATED_OVERLAYS.has(k)) out[k] = ov[k]
  }
  return out
})
// Group ports by direction so the panel reads like a port list in
// an SV declaration — inputs first, then outputs, then inout /
// other. Interface bundles (``port_kind === 'interface'``) have no
// direction and would otherwise pile up under "unknown"; we route
// them to a dedicated "interfaces" group instead. Flattened
// interface signals (``port_kind === 'interface_signal'``) have a
// real ``dir`` pinned from the modport, so they land naturally in
// inputs / outputs. (#102, #105.)
const PORT_DIR_ORDER = ['input', 'output', 'inout', 'interface']
const PORT_DIR_LABEL = {
  input: '输入',
  output: '输出',
  inout: '双向',
  interface: '接口',
}
const portGroups = computed(() => {
  if (!node.value || !Array.isArray(node.value.ports) || node.value.ports.length === 0) {
    return []
  }
  const groups = new Map()
  for (const port of node.value.ports) {
    const dir =
      port.port_kind === 'interface' ? 'interface' : port.dir || 'unknown'
    if (!groups.has(dir)) groups.set(dir, [])
    groups.get(dir).push(port)
  }
  const out = []
  for (const dir of PORT_DIR_ORDER) {
    if (groups.has(dir)) {
      out.push({ dir, label: PORT_DIR_LABEL[dir], ports: groups.get(dir) })
      groups.delete(dir)
    }
  }
  // Any remaining (unknown direction) at the end.
  for (const [dir, ports] of groups) {
    out.push({ dir, label: dir, ports })
  }
  return out
})

function ifaceDescriptor(port) {
  // ``test_mem_if.sub`` when both are present; just the type when no
  // modport; just the modport when no type — should never happen but
  // we degrade gracefully.
  if (port.interface_type && port.modport) return `${port.interface_type}.${port.modport}`
  return port.interface_type || port.modport || 'interface'
}
function ifaceTag(port) {
  return port.port_kind === 'interface_signal'
    ? `（来自 ${ifaceDescriptor(port)}）`
    : `(${ifaceDescriptor(port)})`
}
function ifaceTagTitle(port) {
  return port.port_kind === 'interface_signal'
    ? `由 ${ifaceDescriptor(port)} 展开的信号`
    : `未解析的接口端口：${ifaceDescriptor(port)}`
}
// Openable when the node has either a structured ``source`` block
// (preferred — hub path uses (file, line, col)) or just a raw
// ``link`` URI (offline fallback through the OS).
const hasOpenable = computed(
  () => node.value && (node.value.source || node.value.link),
)
// Absolute ``file:line`` — what ``open_source`` sends, what the copy
// button copies, and what the tooltip shows. Falls back to the raw
// ``link`` URI for nodes with no structured source block.
const absoluteTargetText = computed(() => {
  const src = node.value && node.value.source
  if (src && typeof src.file === 'string') {
    const line = typeof src.start_line === 'number' ? src.start_line : 1
    return `${src.file}:${line}`
  }
  return ''
})
// What the row actually renders: project-relative when we could work
// out a root, ``basename:line`` when we could not.
const openTargetText = computed(() => {
  const src = node.value && node.value.source
  if (src && typeof src.file === 'string') {
    const line = typeof src.start_line === 'number' ? src.start_line : 1
    return `${relativeSourcePath(src.file, store.sourceRoot)}:${line}`
  }
  return (node.value && node.value.link) || ''
})

// --- copy affordances -----------------------------------------------
// Two payloads, one flash: the heading copies the INSTANCE PATH (what
// you paste into ``rb hub send`` or a testbench probe), the source row
// copies the ABSOLUTE ``file:line`` (what you paste into an editor).
const COPY_FLASH_MS = 1200
const copied = ref('')
let copyFlashTimer = null
async function copy(which, text) {
  const ok = await copyText(text)
  if (!ok) return
  copied.value = which
  if (copyFlashTimer) clearTimeout(copyFlashTimer)
  copyFlashTimer = setTimeout(() => {
    copied.value = ''
    copyFlashTimer = null
  }, COPY_FLASH_MS)
}
onBeforeUnmount(() => {
  if (copyFlashTimer) clearTimeout(copyFlashTimer)
})
const openTitle = '源码位置'
</script>

<style scoped>
/* No border-top / margin-top: the enclosing CollapsiblePanel already
   draws the section separator — both drawing it doubled the rule. */
.node-detail { padding: 0.5rem; }
.node-detail h3 {
  font-family: var(--font-mono);
  font-size: 0.85rem;
  margin: 0 0 0.5rem;
  /* Long instance paths (top.u_a.u_b.…) get soft-wrap points at
     each dot via <wbr/>, so the heading wraps where it makes
     visual sense instead of mid-segment. */
  word-break: normal;
  overflow-wrap: break-word;
  line-height: 1.3;
}
.node-detail h3 .sep { color: var(--fg-faint); }
.node-detail dt {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg-muted);
  margin-top: 0.5rem;
}
.node-detail dd { margin: 0.1rem 0 0; font-size: 0.85rem; }
.node-detail dd ul { margin: 0; padding-left: 1rem; }
.node-detail dd pre {
  background: var(--panel-2);
  padding: 0.25rem 0.5rem;
  border-radius: var(--radius-2);
  font-size: 0.75rem;
}
.empty { color: var(--fg-muted); font-size: 0.85rem; }
.nav-actions,
.elsewhere-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-bottom: 0.25rem;
}
.elsewhere-actions {
  align-items: baseline;
  margin-bottom: 0.5rem;
}
/* Same word treatment as a <dt>: this row is a labelled group, not a
   fourth kind of control. */
.elsewhere-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg-muted);
  margin-right: 0.15rem;
}
.nav-actions button,
.elsewhere-actions button {
  border: 1px solid var(--line-strong);
  background: var(--panel);
  color: var(--fg);
  padding: 0.15rem 0.5rem;
  cursor: pointer;
  border-radius: var(--radius-2);
  font-size: 0.75rem;
}
/* Disabled styling is the shared ``button:disabled`` rule in app.css —
   this component used to carry one of four different treatments. */
/* Open-in-editor sits next to the navigation buttons (always at
   the top of the panel) so it doesn't migrate as the per-node
   data section grows/shrinks. The file:line hint lives on its
   own row immediately under so long paths can wrap without
   pushing the buttons around. */
.nav-actions .open-source {
  margin-left: auto;
}
.open-target-row {
  margin-bottom: 0.5rem;
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.open-target {
  font-size: 0.7rem;
  color: var(--fg-muted);
  word-break: break-all;
}
/* Copy affordance — a quiet glyph that only gains a box on hover, so
   two of them on one panel don't read as primary actions. Token
   colours throughout; the ✓ flash is the shared --ok. */
.copy-btn {
  flex-shrink: 0;
  border: 1px solid transparent;
  background: transparent;
  color: var(--fg-faint);
  border-radius: var(--radius-1);
  padding: 0 0.25rem;
  margin-left: 0.3rem;
  font-size: 0.75rem;
  line-height: 1.4;
  cursor: pointer;
  vertical-align: baseline;
}
.copy-btn:hover {
  color: var(--fg);
  border-color: var(--line-strong);
  background: var(--panel-2);
}
/* "(via hub)" / "(via OS)" tag tells the user how the next click
   will be routed — green when the hub is connected and will
   handle the request inline, grey when we'll fall back to a
   ``rtlbuddy://`` URI dispatched through the OS. */
.open-via {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
}
.open-via[data-mode='hub'] {
  background: var(--ok-bg);
  color: var(--ok);
}
.open-via[data-mode='os'] {
  background: var(--panel-2);
  color: var(--fg-muted);
}
.blackbox { color: var(--warn); }
.coverage-block { font-size: 0.78rem; }
.cov-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: 0.15rem 0;
}
.cov-label {
  width: 4.5rem;
  color: var(--fg-muted);
}
.cov-bar {
  flex: 1;
  height: 0.55rem;
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-1);
  overflow: hidden;
  display: inline-block;
}
.cov-bar-fill {
  display: block;
  height: 100%;
}
.cov-nums {
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
/* Physical (#22): three figures on one mono line over the two
   share-of-parent bars, which reuse the coverage bar geometry — they
   are the same control answering a different question. */
.phys-block { font-size: 0.78rem; }
.phys-figures {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-bottom: 0.25rem;
}
.phys-figure { white-space: nowrap; }
.phys-fig-label {
  color: var(--fg-muted);
  margin-right: 0.25rem;
}
.phys-fig-value {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.coverview-link {
  display: inline-block;
  margin-top: 0.25rem;
  font-size: 0.75rem;
  color: var(--accent);
}
/* Live coverage: one mono line of metrics (they are data) over a
   quiet link into the hub's coverage pane for the per-line view. */
.live-cov-metrics {
  font-family: var(--font-mono);
  font-size: 0.78rem;
}
.live-cov-link {
  display: inline-block;
  margin-top: 0.2rem;
  font-size: 0.75rem;
  color: var(--accent);
}
.ports-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}
.ports-table td {
  vertical-align: top;
  padding: 0.05rem 0.25rem 0.05rem 0;
}
.port-name-cell {
  width: 1%;
  white-space: nowrap;
  color: var(--fg);
}
.port-expr-cell {
  color: var(--fg-muted);
  word-break: break-all;
}
.port-expr { color: var(--fg-muted); }
.port-iface-tag {
  margin-left: 0.5em;
  color: var(--warn);        /* matches the block-flow interface tint */
  background: var(--warn-bg);
  font-style: italic;
  font-size: 0.85em;
  padding: 0 0.3em;
  border-radius: var(--radius-1);
}
.axi-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}
.axi-table th {
  text-align: left;
  font-weight: 600;
  color: var(--fg-muted);
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0 0.4em 0.1em 0;
}
.axi-table td {
  padding: 0.1em 0.4em 0.1em 0;
  vertical-align: top;
  white-space: nowrap;
}
.axi-err { color: var(--err); margin-left: 0.25em; }
.axi-role {
  font-size: 0.7rem;
  padding: 0 0.35em;
  border-radius: var(--radius-1);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
/* Initiator reads as the accent, target as a neutral chip. The old
   blue/violet pair spent two hues on a two-value enum and pulled the
   retired indigo in with it. */
.axi-role[data-role='master'] { background: var(--accent); color: var(--accent-contrast); }
.axi-role[data-role='slave'] { background: var(--panel-2); color: var(--fg-muted); }
.axi-tput .rd { color: var(--info); }
.axi-tput .wr { color: var(--warn); margin-left: 0.4em; }
/* Backpressure colours are the shared ``.rb-bp`` ramp in app.css. */
.axi-ic {
  margin-top: 0.3em;
  font-size: 0.75rem;
  color: var(--fg-muted);
}
</style>
