// Live coverage: fetch the hub's ``/cov.json`` and join it to the
// schematic by module name.
//
// This is the LIVE path, and it is deliberately separate from the
// payload-driven ``overlays.coverage`` block that view.json's
// producer can bake into each node (see overlays/coverage.js). The
// two differ in provenance, not in ramp:
//
//   - baked:  whatever the renderer was told at render time; travels
//             with the file, survives embed.py, works offline.
//   - live:   whatever the hub has RIGHT NOW, re-read on load, joined
//             here. Present only when a hub is serving this bundle
//             and it has coverage data.
//
// Everything below degrades to "feature off" rather than to an error:
// opened from embed.py, the Vite dev server or a bare file://, the
// gate global is unset, no fetch happens, and every consumer sees
// ``null`` / an empty Map.

import { coverageColor, coverageNoDataColor } from './palette.js'

/** Same-origin route the hub serves the coverage payload on. */
export const COV_ROUTE = '/cov.json'

/**
 * The injected global that says the hub HAS coverage data.
 *
 * It gates the fetch; it is not the URL we fetch. The hub sets it
 * for the coverage PANE (``/cov``) — the same gate ``hubApps.js``
 * uses for the switcher entry — and the JSON always lives at
 * ``COV_ROUTE`` beside it. Fetching a constant route keeps the two
 * consumers of the global honest about what it means.
 */
export const COV_GATE = '__RTL_BUDDY_COV_URL__'

/**
 * The hub's coverage PANE, where per-file / per-line detail lives.
 * Only ever linked when a hub is serving us — elsewhere it is a 404.
 */
export const COV_PANE_ROUTE = '/cov'

/**
 * LCOV metric buckets, in the order the UI prints them.
 *
 * Names match the payload's ``totals`` keys verbatim; the letters
 * are what NodeDetail renders (``L 85.9% · B 80.5% · …``).
 */
export const COV_METRICS = [
  { key: 'line', letter: 'L' },
  { key: 'branch', letter: 'B' },
  { key: 'toggle', letter: 'T' },
  { key: 'expression', letter: 'E' },
  { key: 'cover', letter: 'C' },
]

/** The metric that drives the canvas tint. */
export const COV_TINT_METRIC = 'line'

const METRIC_KEYS = COV_METRICS.map((m) => m.key)

// ---------------------------------------------------------------------
// Fetch
// ---------------------------------------------------------------------

/** True when a hub that HAS coverage data is serving this bundle. */
export function covServed(win = typeof window !== 'undefined' ? window : null) {
  return Boolean(win && typeof win[COV_GATE] === 'string' && win[COV_GATE].length > 0)
}

/**
 * Fetch and parse ``/cov.json``.
 *
 * Resolves to the payload object, or to ``null`` for every failure
 * mode there is: no hub, no gate global, no fetch, a non-2xx, a body
 * that isn't JSON. The caller has nothing to handle — a null payload
 * simply means the overlay never offers itself.
 */
export async function loadCovData(
  win = typeof window !== 'undefined' ? window : null,
  fetchImpl = null,
) {
  if (!covServed(win)) return null
  // ``win.fetch`` must stay bound to its window — an unbound
  // reference throws "Illegal invocation" in a browser.
  const doFetch =
    fetchImpl ||
    (win && typeof win.fetch === 'function' ? win.fetch.bind(win) : null)
  if (!doFetch) return null
  try {
    const res = await doFetch(COV_ROUTE, { headers: { Accept: 'application/json' } })
    if (!res || !res.ok) return null
    const json = await res.json()
    if (!json || typeof json !== 'object') return null
    return json
  } catch {
    return null
  }
}

// ---------------------------------------------------------------------
// Elaborated → source module names
// ---------------------------------------------------------------------

// Verilator appends a parameterization suffix to every elaborated
// variant of a parameterized module: ``ip_cdc_sync__W4``,
// ``ip_async_fifo__DB13``. view.json's ``node.module`` is always the
// SOURCE name, so the join has to undo exactly one suffix group.
const PARAM_SUFFIX_RE = /__[A-Za-z0-9]+$/

/**
 * The canonical base-name rule, mirroring ``base_module_name`` in
 * rtl_buddy's graph/coverage.py (and the cov pane's ``module-names``
 * block): strip ONE trailing ``__[A-Za-z0-9]+`` group, and only if a
 * non-empty remainder survives.
 *
 * ``__W8`` therefore stays ``__W8`` (stripping leaves nothing), and
 * ``axi__lite__W8`` becomes ``axi__lite`` — one group, not all of
 * them. Names carrying no suffix come back unchanged.
 */
export function baseModuleName(name) {
  if (typeof name !== 'string' || name.length === 0) return ''
  const m = PARAM_SUFFIX_RE.exec(name)
  if (!m) return name
  const base = name.slice(0, m.index)
  return base.length > 0 ? base : name
}

// ---------------------------------------------------------------------
// The join
// ---------------------------------------------------------------------

function emptyBuckets() {
  const out = {}
  for (const key of METRIC_KEYS) out[key] = { found: 0, hit: 0, ratio: null }
  return out
}

function finalize(buckets) {
  for (const key of METRIC_KEYS) {
    const b = buckets[key]
    b.ratio = b.found > 0 ? b.hit / b.found : null
  }
  return buckets
}

function asCount(v) {
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0
}

/**
 * ``Map<moduleName, {line, branch, toggle, expression, cover}>`` for
 * a ``/cov.json`` payload, each bucket ``{found, hit, ratio}``.
 *
 * Walks ``files[]``. Every module a file declares is keyed BOTH under
 * its elaborated name and under its base name, which is what gives
 * lookups "exact match first, stripped second" for free: a source
 * module genuinely called ``axi__lite__W8`` finds its own bucket,
 * while ``ip_cdc_sync`` finds the union of ``__W4`` and ``__W8``.
 * Keys are deduped per file, so a file listing two variants of the
 * same module doesn't count its totals twice into the base bucket.
 *
 * APPROXIMATION (reviewable): coverage arrives per FILE, and a file's
 * totals are attributed IN FULL to every module the file declares.
 * In this codebase files are ~one module each, so the attribution is
 * effectively exact; in a file holding several modules each of them
 * would report the file's aggregate rather than its own share.
 * Splitting properly needs per-module line ranges, which the payload
 * doesn't carry.
 *
 * ``ratio`` is recomputed from the summed counts, never averaged
 * across files — averaging ratios weights a 3-line file the same as
 * a 300-line one. ``found === 0`` yields ``null``, not ``0``, so
 * "no data" stays distinguishable from "nothing covered".
 */
export function moduleCoverage(covJson) {
  const out = new Map()
  const files = covJson && Array.isArray(covJson.files) ? covJson.files : []
  for (const file of files) {
    const totals = file && file.totals
    if (!totals || typeof totals !== 'object') continue
    const keys = new Set()
    for (const m of Array.isArray(file.modules) ? file.modules : []) {
      if (typeof m !== 'string' || m.length === 0) continue
      keys.add(m)
      keys.add(baseModuleName(m))
    }
    for (const key of keys) {
      let bucket = out.get(key)
      if (!bucket) {
        bucket = emptyBuckets()
        out.set(key, bucket)
      }
      for (const metric of METRIC_KEYS) {
        const t = totals[metric]
        if (!t || typeof t !== 'object') continue
        bucket[metric].found += asCount(t.found)
        bucket[metric].hit += asCount(t.hit)
      }
    }
  }
  for (const bucket of out.values()) finalize(bucket)
  return out
}

// ---------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------

/**
 * The shared coverage ramp, as a percentage → colour.
 *
 * ``null`` / non-numeric is the explicit no-data grey, which is what
 * makes an uncovered-but-known module (red) read differently from a
 * module LCOV never saw (grey). The ramp itself is the token sheet's
 * documented continuous fill — red at 0, amber at 50, green at 100 —
 * resolved through ``palette.js`` so the schematic, the graph pane
 * and the coverage app stay on one ramp under both themes.
 */
export function covColor(pct) {
  if (typeof pct !== 'number' || !Number.isFinite(pct)) return coverageNoDataColor()
  return coverageColor(pct)
}

/** ``0..1`` ratio (or null) → ``0..100`` percentage (or null). */
export function ratioPct(ratio) {
  if (typeof ratio !== 'number' || !Number.isFinite(ratio)) return null
  return ratio * 100
}

/**
 * One-line metric summary for a bucket, e.g.
 * ``L 85.9% · B 80.5% · T 78.9%``. Metrics with no data are skipped
 * rather than printed as ``0%`` — an absent toggle run is not a
 * failing toggle score.
 */
export function covSummaryText(buckets) {
  if (!buckets) return ''
  const parts = []
  for (const { key, letter } of COV_METRICS) {
    const pct = ratioPct(buckets[key] && buckets[key].ratio)
    if (pct === null) continue
    parts.push(`${letter} ${pct.toFixed(1)}%`)
  }
  return parts.join(' · ')
}

/**
 * ``generated_at`` rendered as a plain ``YYYY-MM-DD`` day.
 *
 * Sliced off an ISO-shaped prefix rather than round-tripped through
 * ``Date``, for two reasons: ``Date`` shifts the day across a
 * timezone boundary, and it is startlingly willing to invent one —
 * ``new Date('run 42')`` is a valid date in 2042. Anything that
 * doesn't look like a date is passed through verbatim; the panel
 * would rather print the producer's own words than a fiction.
 */
export function covGeneratedDate(covJson) {
  const raw = covJson && covJson.generated_at
  if (typeof raw !== 'string' || raw.length === 0) return ''
  return /^\d{4}-\d{2}-\d{2}/.test(raw) ? raw.slice(0, 10) : raw
}
