/** Fit only visible, settled canvas sizes, including iframe reveal and split-pane transitions. */
export function createViewportFitter(host, fit, {
  requestFrame = requestAnimationFrame,
  cancelFrame = cancelAnimationFrame,
  Observer = globalThis.ResizeObserver,
} = {}) {
  let frame = null, measured = null, disposed = false
  /** Wait for two matching measurements rather than fitting during a hidden or changing layout. */
  function tick() {
    frame = null
    if (disposed || !host.isConnected) return
    const rect = host.getBoundingClientRect()
    if (!Number.isFinite(rect.width) || !Number.isFinite(rect.height) || rect.width <= 1 || rect.height <= 1) {
      measured = null
      return
    }
    if (!measured || Math.abs(rect.width - measured.width) > 0.5 || Math.abs(rect.height - measured.height) > 0.5) {
      measured = { width: rect.width, height: rect.height }
      frame = requestFrame(tick)
      return
    }
    fit()
  }
  /** Coalesce observer, host-message and renderer requests into one settled fit. */
  function request() {
    if (disposed) return
    measured = null
    if (frame !== null) cancelFrame(frame)
    frame = requestFrame(tick)
  }
  const observer = Observer ? new Observer(request) : null
  observer?.observe(host)
  if (!observer && typeof window !== 'undefined') window.addEventListener('resize', request)
  request()
  return {
    request,
    disconnect() {
      disposed = true
      observer?.disconnect()
      if (!observer && typeof window !== 'undefined') window.removeEventListener('resize', request)
      if (frame !== null) cancelFrame(frame)
      frame = null
    },
  }
}
