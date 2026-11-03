// Pan / zoom for an SVG canvas — the same hand-rolled transform
// GraphCanvas has always used, lifted so a second canvas cannot
// drift from it.
//
// Hand-rolled rather than a library because the dependency budget
// for the single-file standalone HTML is tight (embed.py inlines the
// whole bundle) and viz.js + elkjs + vue + pinia already dominate it.
// Wheel zooms about the cursor, click-drag pans, and the automatic
// fit is capped by FIT_SCALE_MAX so a two-node scope doesn't become
// a billboard.
//
// GraphCanvas keeps its own copy for now: its fit step first has to
// undo the intrinsic ``viewBox`` / ``width`` / ``height`` viz.js
// bakes into the SVG string it emits, which a Vue-owned SVG never
// has. Folding it in is a follow-up, not a prerequisite — see
// rtl-buddy-sch#163.

import { onBeforeUnmount, onMounted, ref } from 'vue'
import { FIT_SCALE_MAX } from '../layout/constants.js'

const MIN_SCALE = 0.1
const MAX_SCALE = 10
const WHEEL_FACTOR = 1.1
const BUTTON_FACTOR = 1.2

/**
 * @param {object} opts
 * @param {import('vue').Ref} opts.hostEl  the scrolling/clipping host
 * @param {() => SVGGElement|null} opts.getRoot  the <g> that carries
 *        the transform. A getter, not a ref: the element can be
 *        replaced by a re-render between calls.
 * @param {() => {x:number,y:number,width:number,height:number}|null} opts.getContentBox
 *        content bounds in user space, for ``fit``.
 */
export function usePanZoom({ hostEl, getRoot, getContentBox }) {
  const transform = ref({ x: 0, y: 0, scale: 1 })
  let dragStart = null

  function apply() {
    const root = getRoot()
    if (!root) return
    const { x, y, scale } = transform.value
    root.setAttribute('transform', `translate(${x},${y}) scale(${scale})`)
  }

  function hostRect() {
    const el = hostEl.value
    if (!el || typeof el.getBoundingClientRect !== 'function') return null
    return el.getBoundingClientRect()
  }

  function onWheel(e) {
    const rect = hostRect()
    if (!rect) return
    e.preventDefault()
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top
    const factor = e.deltaY < 0 ? WHEEL_FACTOR : 1 / WHEEL_FACTOR
    const clamped = Math.max(
      MIN_SCALE,
      Math.min(MAX_SCALE, transform.value.scale * factor),
    )
    const ratio = clamped / transform.value.scale
    transform.value = {
      scale: clamped,
      x: cx - (cx - transform.value.x) * ratio,
      y: cy - (cy - transform.value.y) * ratio,
    }
    apply()
  }

  function onMouseDown(e) {
    if (e.button !== 0) return
    dragStart = {
      x: e.clientX,
      y: e.clientY,
      tx: transform.value.x,
      ty: transform.value.y,
    }
  }

  function onMouseMove(e) {
    if (!dragStart) return
    transform.value = {
      ...transform.value,
      x: dragStart.tx + (e.clientX - dragStart.x),
      y: dragStart.ty + (e.clientY - dragStart.y),
    }
    apply()
  }

  function onMouseUp() {
    dragStart = null
  }

  function zoomIn() {
    transform.value = {
      ...transform.value,
      scale: Math.min(MAX_SCALE, transform.value.scale * BUTTON_FACTOR),
    }
    apply()
  }

  function zoomOut() {
    transform.value = {
      ...transform.value,
      scale: Math.max(MIN_SCALE, transform.value.scale / BUTTON_FACTOR),
    }
    apply()
  }

  function reset() {
    transform.value = { x: 0, y: 0, scale: 1 }
    apply()
  }

  function fit() {
    const rect = hostRect()
    const bb = getContentBox()
    if (!rect || !bb || !bb.width || !bb.height || !rect.width || !rect.height) {
      apply()
      return
    }
    const scale = Math.min(
      rect.width / bb.width,
      rect.height / bb.height,
      FIT_SCALE_MAX,
    )
    transform.value = {
      scale,
      x: (rect.width - bb.width * scale) / 2 - bb.x * scale,
      y: (rect.height - bb.height * scale) / 2 - bb.y * scale,
    }
    apply()
  }

  /** Centre ``box`` (user space) without changing zoom. */
  function panTo(box) {
    const rect = hostRect()
    if (!rect || !box || !box.width || !box.height) return
    const scale = transform.value.scale
    transform.value = {
      scale,
      x: rect.width / 2 - (box.x + box.width / 2) * scale,
      y: rect.height / 2 - (box.y + box.height / 2) * scale,
    }
    apply()
  }

  /**
   * Recentre only when the box's centre is currently off-screen.
   * Selecting a node must never rescale the canvas — clicking a large
   * block used to zoom out to "fit" it, which reads as a disorienting
   * jump. An already-visible selection is left untouched.
   */
  function bringIntoView(box) {
    const rect = hostRect()
    if (!rect || !box || !box.width || !box.height) return
    const { scale, x, y } = transform.value
    const cx = (box.x + box.width / 2) * scale + x
    const cy = (box.y + box.height / 2) * scale + y
    if (cx >= 0 && cx <= rect.width && cy >= 0 && cy <= rect.height) return
    panTo(box)
  }

  function attach() {
    const el = hostEl.value
    if (el) {
      el.addEventListener('wheel', onWheel, { passive: false })
      el.addEventListener('mousedown', onMouseDown)
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }

  function detach() {
    const el = hostEl.value
    if (el) {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('mousedown', onMouseDown)
    }
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  onMounted(attach)
  onBeforeUnmount(detach)

  return {
    transform,
    apply,
    fit,
    reset,
    zoomIn,
    zoomOut,
    panTo,
    bringIntoView,
    // Exposed because the host element can be behind a ``v-if``: a
    // canvas that mounted into its empty state has no host to listen
    // on, and would stay un-pannable for the rest of the session once
    // data arrived. Callers re-attach when the host appears.
    attach,
    detach,
  }
}
