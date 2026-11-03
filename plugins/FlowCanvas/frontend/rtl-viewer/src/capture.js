// Graph-only image capture. Serializes the SVG currently rendered by
// GraphCanvas to PNG (via an offscreen canvas) or raw SVG bytes.
//
// Surfaced two ways:
//   1. ``view_capture`` request envelope handler in useHub.js — lets
//      the hub (and any peer connected to it, including ``rb hub send
//      capture``) ask the SPA for a snapshot.
//   2. Future in-app "Save image" button — same entry point.
//
// Graph-only by design: side panels, toolbar, and the overlay legend
// are not included. v1 scope (rtl_buddy hub-side capture feature).

import { token, TOKEN_FALLBACKS } from './theme.js'

// Cap PNG rasterisation so the hub never has to wait out its own
// 15s envelope timeout when the browser's Image+canvas pipeline
// wedges (issue #101). The wedge has been observed after a hub
// stop/start followed by an SPA hard-reload: neither ``onload``
// nor ``onerror`` ever fires. A bounded reject turns that into a
// prompt error envelope so peers see a real failure instead of a
// dead request.
const PNG_RASTERISE_TIMEOUT_MS = 5000

let _svgProvider = null

/**
 * GraphCanvas calls this on mount with a callable that returns its
 * current SVG element (or null if nothing is rendered yet). The
 * function form — instead of a ref to the element — keeps the
 * indirection alive across re-renders: viz.js rewrites ``innerHTML``
 * on every graph change, so the underlying <svg> identity moves.
 */
export function registerSvgProvider(fn) {
  _svgProvider = typeof fn === 'function' ? fn : null
}

export function unregisterSvgProvider(fn) {
  if (_svgProvider === fn) _svgProvider = null
}

/**
 * Returns the SVG element currently held by GraphCanvas, or null
 * when no graph is rendered (and therefore no capture is possible).
 */
export function currentGraphSvg() {
  return _svgProvider ? _svgProvider() : null
}

/**
 * Capture the current graph as a PNG (default) or SVG string.
 *
 * Returns ``{format, bytes_b64, width, height}``. Throws when no
 * graph is rendered, the format is unknown, or PNG rasterisation
 * fails (e.g. an in-SVG ``<image href=...>`` that violates CORS —
 * not used today but worth knowing).
 *
 * ``scale`` upsamples the PNG raster — useful for slide decks. SVG
 * ignores ``scale`` (vector).
 */
export async function captureGraphImage(
  format = 'png',
  { scale = 1, timeoutMs = PNG_RASTERISE_TIMEOUT_MS } = {},
) {
  const svg = currentGraphSvg()
  if (!svg) {
    throw new Error('no graph rendered — load a view.json first')
  }
  const xml = serializeSvg(svg)
  const { width, height } = svgDimensions(svg)
  if (format === 'svg') {
    return {
      format: 'svg',
      bytes_b64: utf8ToBase64(xml),
      width,
      height,
    }
  }
  if (format !== 'png') {
    throw new Error(`unsupported capture format: ${format}`)
  }
  const w = Math.max(1, Math.ceil(width * scale))
  const h = Math.max(1, Math.ceil(height * scale))
  const dataUrl = await renderSvgToPng(xml, w, h, timeoutMs, backdropFor(svg))
  // ``data:image/png;base64,XXXX`` → ``XXXX``.
  const idx = dataUrl.indexOf(',')
  const bytes_b64 = idx >= 0 ? dataUrl.slice(idx + 1) : dataUrl
  return { format: 'png', bytes_b64, width: w, height: h }
}

// --- internals ---

/**
 * Pick the opaque plate the PNG is composited onto.
 *
 * Normally the ACTIVE theme's ``--bg``: the DOT builders resolve tokens
 * at build time, so a dark-theme graph already carries dark-theme
 * colours and would be pale text on a white plate.
 *
 * The producer-supplied layout DOT is the exception. It is baked light
 * and stays legible on screen only because of GraphCanvas's ``:deep``
 * re-tint rules — and document CSS does not travel with the
 * ``cloneNode`` + ``XMLSerializer`` standalone SVG we rasterise. On a
 * dark plate that capture is black text on near-black, so pin it to the
 * light ``--bg`` the producer baked for. ``renderSvg`` marks the host
 * ``.producer-dot`` exactly when that DOT is what got laid out.
 *
 * Exported so the rule is testable without driving Image + canvas to
 * completion, which happy-dom cannot do.
 */
export function backdropFor(svg) {
  const host = typeof svg.closest === 'function' ? svg.closest('.svg-host') : null
  if (host && host.classList.contains('producer-dot')) {
    return TOKEN_FALLBACKS['--bg']
  }
  return token('--bg')
}

function serializeSvg(svg) {
  // Clone so we can patch xmlns + dimensions without mutating the
  // live SVG. ``cloneNode(true)`` is a deep copy of the DOM — viz.js's
  // generated nodes use plain attributes (no shadow DOM), so the
  // clone reproduces correctly.
  const clone = svg.cloneNode(true)
  if (!clone.getAttribute('xmlns')) {
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  }
  if (!clone.getAttribute('xmlns:xlink')) {
    clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  }
  // viz.js often emits ``width="…pt"`` / ``height="…pt"``. For
  // standalone use we want the viewBox to be the source of truth; a
  // PNG-side raster scale beats hard-coded points.
  const { width, height } = svgDimensions(svg)
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  return new XMLSerializer().serializeToString(clone)
}

function svgDimensions(svg) {
  const vb = svg.getAttribute('viewBox')
  if (vb) {
    const parts = vb.trim().split(/\s+/).map(Number)
    if (parts.length === 4 && parts.every((n) => Number.isFinite(n))) {
      return { width: parts[2], height: parts[3] }
    }
  }
  const w = parseFloat(svg.getAttribute('width')) || svg.clientWidth || 800
  const h = parseFloat(svg.getAttribute('height')) || svg.clientHeight || 600
  return { width: w, height: h }
}

/**
 * SVG XML → a ``data:image/png;base64,…`` URL, via Image + canvas.
 *
 * Exported because the schematic canvas's export button (#163 P5)
 * rasterises the same way and the interesting parts are the failure
 * modes, not the happy path: the bounded timeout below is the only
 * thing standing between a wedged Image pipeline and a UI that never
 * answers. A second copy would be a second place to forget that.
 */
export function renderSvgToPng(
  svgXml,
  width,
  height,
  timeoutMs = PNG_RASTERISE_TIMEOUT_MS,
  backdrop = null,
) {
  return new Promise((resolve, reject) => {
    const blob = new Blob([svgXml], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const img = new Image()
    // ``settled`` guards against the race where (rare) onload and the
    // timeout fire on top of each other — without it we'd double-call
    // resolve/reject and double-free the object URL.
    let settled = false
    let timer = null
    const finish = (fn, arg) => {
      if (settled) return
      settled = true
      if (timer !== null) clearTimeout(timer)
      try {
        URL.revokeObjectURL(url)
      } catch {
        // jsdom / older browsers may throw if the URL was already
        // revoked or never registered. Either way the cleanup is best
        // effort; the actual resolve/reject is the contract.
      }
      fn(arg)
    }
    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        // Diagnostic: dump a head sample so the user can paste it
        // into the issue if this fires repeatably. Bounded to keep
        // the console noise from drowning the rest of the session;
        // ``rb hub send capture --out /tmp/x.svg`` round-trips the
        // full XML when more is needed.
        const SAMPLE_BYTES = 800
        const sample =
          svgXml.length > SAMPLE_BYTES ? svgXml.slice(0, SAMPLE_BYTES) + '…' : svgXml
        // eslint-disable-next-line no-console
        console.warn(
          `[rtl-buddy-view] view_capture: PNG rasterise timed out after ${timeoutMs}ms ` +
            `(${svgXml.length} bytes of SVG). Returning an error envelope so the hub ` +
            `request doesn't wedge. Try \`rb hub send capture --out /tmp/x.svg\` to ` +
            `bypass the canvas path. SVG head: ${sample}`,
        )
        finish(reject, new Error(`PNG rasterise timed out after ${timeoutMs}ms`))
      }, timeoutMs)
    }
    img.onload = () => {
      finish((u) => {
        try {
          const canvas = document.createElement('canvas')
          canvas.width = width
          canvas.height = height
          const ctx = canvas.getContext('2d')
          // Solid backdrop — the SVG has no background and most
          // consumers (slides, docs, agent reads) prefer opaque PNGs
          // to checker-pattern transparency.
          //
          // It follows the ACTIVE theme rather than being pinned
          // white, because the graph's own colours are already baked
          // for that theme: the DOT builders resolve tokens at build
          // time, so a dark-theme capture on a white plate is pale
          // text on white. A caller that wants a light plate flips the
          // theme first — the canvas re-lays-out on the flip.
          // (``backdropFor`` overrides this for the producer DOT.)
          ctx.fillStyle = backdrop || token('--bg')
          ctx.fillRect(0, 0, width, height)
          ctx.drawImage(img, 0, 0, width, height)
          resolve(canvas.toDataURL('image/png'))
        } catch (err) {
          reject(err)
        }
      }, url)
    }
    img.onerror = () => {
      finish(reject, new Error('failed to rasterise SVG (image load error)'))
    }
    img.src = url
  })
}

function utf8ToBase64(str) {
  // ``btoa`` is latin-1 only; UTF-8 names in the SVG would corrupt.
  const bytes = new TextEncoder().encode(str)
  let bin = ''
  // Chunked to avoid call-stack blowups on very large strings.
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK))
  }
  return btoa(bin)
}
