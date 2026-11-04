<template>
  <section class="node-detail" v-if="node">
    <div class="node-summary">
      <h3 :title="node.id + ' · 模块：' + node.module"><span class="inst-path">{{ node.id }}</span></h3>
      <div v-if="hasOpenable" class="open-target-row">
        <button type="button" class="open-target" :title="'查看源码 · ' + absoluteTargetText" @click="openSource">{{ openTargetText }}</button>
      </div>
      <button class="detail-ai-explain" type="button" :aria-label="'AI 解释设计 '+node.module" title="AI 解释设计" @mouseenter="explainDesign(node,$event,'preview')" @mouseleave="explainDesign(node,$event,'leave')" @click.stop="explainDesign(node,$event,'pin')" @dblclick.stop @keydown.stop><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6M7 9h4M7 13h8M7 17h5"/><path class="design-ai-spark" d="m18 1 1.4 4.6L24 7l-4.6 1.4L18 13l-1.4-4.6L12 7l4.6-1.4Z"/></svg></button>
    </div>
    <div v-if="node.module !== node.id.split('.').at(-1)" class="node-module-caption" :title="'模块：' + node.module">{{ node.module }}</div>
    <dl>
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
        <dd class="ports-scroll" tabindex="0" :aria-label="group.label + '端口连接表，可横向滚动'">
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
import { computed } from 'vue'
import { useViewerStore } from '../store.js'
import { relativeSourcePath } from '../sourcePaths.js'
import { heatColor } from '../overlays/coverage.js'
import { covSummaryText, COV_PANE_ROUTE } from '../covData.js'
import { displayOrigin } from '../displayNames.js'
import { bpLevel, heatRampColor } from '../palette.js'
import { areaOf, physScope, powerOf } from '../overlays/phys.js'
import { themeVersion } from '../theme.js'
import { formatBandwidth as fmtBps } from '../format.js'

/** Ask the host to compose a task using this exact instance and source location. */
function explainDesign(row,event,action){
  const rect=event.currentTarget.getBoundingClientRect(),node=store.nodesById.get(row.id)
  // Pinia source records are reactive proxies; postMessage cannot clone them.
  const source=node?.source ? JSON.parse(JSON.stringify(node.source)) : null
  window.parent.postMessage({type:'flowcanvas-explain-design',action,instance:row.id,module:row.module,source,rect:{left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom}},location.origin)
}

const store = useViewerStore()
const node = computed(() => store.selectedNode)
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
// Source links use the host's project file viewer.
const hasOpenable = computed(() => typeof node.value?.source?.file === 'string')
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

/** Open the selected source in the host viewer without leaving the design. */
function openSource(){
  const source=node.value?.source
  if(!source?.file)return
  window.parent.postMessage({type:'flowcanvas-open-source',path:source.file,line:source.start_line||1,end:source.end_line||source.start_line||1},location.origin)
}
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
.ports-scroll {
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: thin;
}
.ports-table {
  width: max-content;
  min-width: 100%;
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
  white-space: nowrap;
  word-break: normal;
  overflow-wrap: normal;
}
.ports-table code { white-space: nowrap; word-break: normal; overflow-wrap: normal; }
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

<style scoped>
/* Match FlowCanvas's quiet outline actions; reserve geometry while copy feedback changes. */
.node-detail h3{display:flex;align-items:center;gap:6px}
.inst-path{min-width:0}
.open-target-row{align-items:center;gap:6px}
.copy-btn{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;padding:4px;margin:0;border:0;border-radius:6px;line-height:1;color:var(--fg-muted);background:transparent}
.copy-btn svg,.nav-actions svg{display:block;width:15px;height:15px;flex:none;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.copy-btn:hover,.nav-actions button:hover:enabled{color:var(--fg);background:color-mix(in srgb,var(--fg) 5%,var(--panel))}
.copy-btn:focus-visible,.nav-actions button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.nav-actions{align-items:center;gap:4px;margin:0 0 8px}
.nav-actions button{display:inline-flex;align-items:center;justify-content:center;gap:5px;height:27px;padding:4px 7px;border:0;border-radius:6px;background:transparent;color:var(--fg-muted);font:11px/1 system-ui,sans-serif;white-space:nowrap}
.nav-actions button:disabled{opacity:.35;cursor:default}
</style>

<style scoped>
/* Compact identity bar: metadata yields space before the fixed navigation controls. */
.node-summary{display:flex;align-items:center;gap:8px;min-width:0;margin-bottom:6px}
.node-summary h3{order:0;flex:0 1 auto;min-width:45px;margin:0;gap:2px;font-size:12px;line-height:24px;white-space:nowrap}
.node-summary .inst-path{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;overflow-wrap:normal}
.node-summary .inst-path wbr{display:none}
.node-summary .open-target-row{order:1;flex:0 1 auto;min-width:0;flex-wrap:nowrap;margin:0;gap:2px}
.node-summary .open-target{font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;word-break:normal}
.node-summary .copy-btn{width:20px;height:22px;padding:3px;opacity:0;transition:opacity .15s}
.node-summary:hover .copy-btn,.node-summary:focus-within .copy-btn{opacity:1}
.node-summary .nav-actions{order:2;flex:none;flex-wrap:nowrap;margin:0 0 0 auto;gap:1px}
.node-summary .nav-actions button{width:24px;height:24px;padding:4px}
.node-summary .nav-actions button span{display:none}
.node-module-caption{font:10px/1.5 var(--font-mono);color:var(--fg-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:6px}
.node-detail dl{margin-top:4px}
@media(hover:none){.node-summary .copy-btn{opacity:1}}
</style>

<style scoped>
.node-summary .open-target{border:0;background:transparent;padding:2px 0;color:var(--fg-muted);font:10px/1.5 var(--font-mono);cursor:pointer;text-align:left}
.node-summary .open-target:hover{color:var(--accent);text-decoration:underline;text-underline-offset:3px}
.node-summary .open-target:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
</style>

<style scoped>
.node-summary .detail-ai-explain{order:2;display:grid;place-items:center;flex:0 0 24px;width:24px;height:24px;margin-left:auto;padding:4px;border:0;border-radius:6px;background:transparent;color:var(--fg-muted);cursor:pointer}
.detail-ai-explain svg{display:block;width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;overflow:visible}
.detail-ai-explain .design-ai-spark{fill:#9466e8;stroke:#6e83f4;stroke-width:.7;transform-box:fill-box;transform-origin:center;transition:transform .25s}
.detail-ai-explain:hover{color:var(--accent);background:color-mix(in srgb,var(--fg) 5%,var(--panel))}
.detail-ai-explain:hover .design-ai-spark{transform:rotate(10deg) scale(1.1)}
.detail-ai-explain:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media(prefers-reduced-motion:reduce){.detail-ai-explain .design-ai-spark{transition:none}}
</style>
