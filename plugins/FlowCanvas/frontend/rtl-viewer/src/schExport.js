// Static export of the schematic canvas (#163 P5).
//
// The hub-side ``view_capture`` path in ``capture.js`` serialises
// whatever GraphCanvas rendered and hands the bytes back over the wire.
// This is the other direction: a button in the schematic toolbar that
// writes a file to the user's downloads, for a datasheet or a review
// deck. Three things make it more than "serialise the SVG":
//
//  1. **Self-contained.** The live canvas is styled by a scoped
//     stylesheet full of ``var(--…)``. Document CSS does not travel
//     with ``cloneNode`` + ``XMLSerializer``, so an exported file
//     styled that way is a black-on-black pile of default SVG paint.
//     Every element's computed presentation properties are inlined as
//     literals before serialisation.
//
//  2. **On a white plate, whatever the app is wearing.** Computed
//     style follows the *active* theme, so exporting from dark mode
//     would inline pale strokes and then put them on white. The whole
//     serialisation therefore runs inside {@link withLightTheme},
//     which pins ``data-theme="light"`` for the duration — one
//     synchronous task, so the browser never paints the intermediate
//     state and the user sees no flash.
//
//  3. **No pan/zoom baked in.** The root ``<g>`` carries the viewer's
//     transform; an exported sheet is not panned. The clone drops it
//     and takes its extent from the sheet frame instead.
//
// No new dependency: Blob + XMLSerializer + an ``<a download>`` are
// all DOM. PNG reuses ``capture.js``'s bounded rasteriser.

import { renderSvgToPng } from './capture.js'
import { TOKEN_FALLBACKS } from './theme.js'

const SVG_NS = 'http://www.w3.org/2000/svg'

/** PNG upsample factor. 2x is the print/retina default. */
export const PNG_SCALE = 2

/**
 * Slack added around the sheet frame in the exported viewBox.
 *
 * The frame's border is drawn *on* the frame box, and a stroke is
 * centred on its path — so without the bleed the outer half of the
 * border falls outside the viewBox and the exported sheet has a
 * hairline border on three sides and none on the fourth.
 */
export const EXPORT_BLEED = 2

/**
 * The plate an exported drawing sits on.
 *
 * ``--panel`` (pure white in the light palette), not ``--bg`` (the
 * app's faintly-blue canvas): a schematic goes into a document, and a
 * document's page is white.
 */
export const EXPORT_BACKGROUND = TOKEN_FALLBACKS['--panel']

/**
 * Presentation properties inlined onto every element.
 *
 * A deliberately short list rather than the whole computed style:
 * dumping all ~340 properties inflates the file by an order of
 * magnitude and drags in layout properties that mean nothing in a
 * standalone SVG. ``filter`` is left out on purpose — the selection
 * halo is viewer state, not part of the drawing.
 */
export const INLINE_STYLE_PROPS = Object.freeze([
  'fill',
  'fill-opacity',
  'stroke',
  'stroke-width',
  'stroke-dasharray',
  'stroke-linecap',
  'stroke-linejoin',
  'opacity',
  'font-family',
  'font-size',
  'font-weight',
  // The algebraic bus width's only distinguishing mark is its italic
  // (SchematicCanvas ``.sch-slash-text.symbolic``); dropping it here
  // would silently turn ``/PTR_W`` into a claim that it is a count.
  'font-style',
  'text-anchor',
  'dominant-baseline',
])

/** A box grown by {@link EXPORT_BLEED} on every side. */
export function bleedBox(box) {
  return {
    x: box.x - EXPORT_BLEED,
    y: box.y - EXPORT_BLEED,
    width: box.width + EXPORT_BLEED * 2,
    height: box.height + EXPORT_BLEED * 2,
  }
}

/** ``top`` + extension → a stable, filesystem-safe download name. */
export function exportFileName(top, ext) {
  const stem = String(top || 'schematic').replace(/[^A-Za-z0-9._-]+/g, '_')
  return `${stem}-schematic.${ext}`
}

/**
 * Run ``fn`` with the light palette pinned, then restore the pin.
 *
 * Restoring the *previous attribute value* (including its absence,
 * which is how the SPA spells "follow the OS") matters: the header's
 * theme control is three-state, and clobbering ``system`` into
 * ``light`` on every export would be a bug the user only notices at
 * sunset.
 */
export function withLightTheme(fn) {
  const el = typeof document !== 'undefined' ? document.documentElement : null
  if (!el) return fn()
  const previous = el.getAttribute('data-theme')
  el.setAttribute('data-theme', 'light')
  try {
    return fn()
  } finally {
    if (previous === null) el.removeAttribute('data-theme')
    else el.setAttribute('data-theme', previous)
  }
}

/**
 * Copy computed presentation properties from ``live`` onto ``clone``,
 * recursing over both trees in lockstep.
 *
 * The pair-walk is why the clone must not be restructured first: the
 * two trees are indexed positionally, because a clone is not in the
 * document and therefore has no computed style of its own to read.
 */
export function inlineComputedStyles(live, clone) {
  if (!live || !clone) return clone
  const view = live.ownerDocument && live.ownerDocument.defaultView
  if (!view || typeof view.getComputedStyle !== 'function') return clone
  const walk = (a, b) => {
    const cs = view.getComputedStyle(a)
    if (cs) {
      const decls = []
      for (const prop of INLINE_STYLE_PROPS) {
        const value = cs.getPropertyValue(prop)
        if (value && value !== 'none' && value !== 'normal') {
          decls.push(`${prop}:${value.trim()}`)
        } else if (value === 'none' && (prop === 'fill' || prop === 'stroke')) {
          // ``none`` is a real decision for fill/stroke (an unfilled
          // wire path); for everything else it is just the default.
          decls.push(`${prop}:none`)
        }
      }
      if (decls.length > 0) b.setAttribute('style', decls.join(';'))
    }
    const ac = a.children || []
    const bc = b.children || []
    for (let i = 0; i < ac.length && i < bc.length; i++) walk(ac[i], bc[i])
  }
  walk(live, clone)
  return clone
}

/**
 * The live SVG as a standalone document string.
 *
 * ``viewBox`` is the sheet frame (see ``elkSchematic.sheetFrame``),
 * not the SVG's own attributes: the frame is drawn in negative
 * coordinates around the laid-out extent, so the element's ``width`` /
 * ``height`` would clip the border off the file.
 */
export function standaloneSvgString(svgEl, { viewBox, background = EXPORT_BACKGROUND } = {}) {
  if (!svgEl) throw new Error('no schematic rendered')
  const clone = svgEl.cloneNode(true)
  inlineComputedStyles(svgEl, clone)
  const box = bleedBox(viewBox || { x: 0, y: 0, width: 800, height: 600 })
  const width = Math.max(1, Math.ceil(box.width))
  const height = Math.max(1, Math.ceil(box.height))
  clone.setAttribute('xmlns', SVG_NS)
  clone.setAttribute('viewBox', `${box.x} ${box.y} ${box.width} ${box.height}`)
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  const root = clone.querySelector('g')
  if (root) root.removeAttribute('transform')
  // Opaque plate, inserted after the style pass so the pair-walk sees
  // identical trees.
  const plate = clone.ownerDocument.createElementNS(SVG_NS, 'rect')
  plate.setAttribute('x', String(box.x))
  plate.setAttribute('y', String(box.y))
  plate.setAttribute('width', String(box.width))
  plate.setAttribute('height', String(box.height))
  plate.setAttribute('fill', background)
  clone.insertBefore(plate, clone.firstChild)
  return new XMLSerializer().serializeToString(clone)
}

/** Hand ``blob`` to the browser's download machinery as ``fileName``. */
export function downloadBlob(blob, fileName) {
  const url = URL.createObjectURL(blob)
  try {
    downloadUrl(url, fileName)
  } finally {
    // Revoked on the next turn, not immediately: the click is
    // dispatched synchronously but the fetch of the object URL is not.
    setTimeout(() => {
      try {
        URL.revokeObjectURL(url)
      } catch {
        /* already revoked / never registered */
      }
    }, 0)
  }
}

/** Click a synthetic ``<a download>``. Works for blob: and data: URLs. */
export function downloadUrl(url, fileName) {
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  a.rel = 'noopener'
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/**
 * Download the schematic as a standalone ``.svg``.
 *
 * Returns the XML so a caller (or a test) can assert on it without
 * re-reading the file.
 */
export function exportSchematicSvg(svgEl, { viewBox, fileName }) {
  const xml = withLightTheme(() => standaloneSvgString(svgEl, { viewBox }))
  downloadBlob(new Blob([xml], { type: 'image/svg+xml;charset=utf-8' }), fileName)
  return xml
}

/** Download the schematic as a ``.png``, upsampled by ``scale``. */
export async function exportSchematicPng(
  svgEl,
  { viewBox, fileName, scale = PNG_SCALE },
) {
  const xml = withLightTheme(() => standaloneSvgString(svgEl, { viewBox }))
  // Rasterise the box the XML actually declares, bleed included —
  // otherwise the PNG is drawn at a slightly different aspect ratio
  // than the SVG it came from.
  const box = bleedBox(viewBox)
  const width = Math.max(1, Math.ceil(box.width * scale))
  const height = Math.max(1, Math.ceil(box.height * scale))
  const dataUrl = await renderSvgToPng(xml, width, height, undefined, EXPORT_BACKGROUND)
  downloadUrl(dataUrl, fileName)
  return { width, height }
}
