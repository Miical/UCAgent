// One-liner clipboard write with a legacy fallback.
//
// ``navigator.clipboard`` is unavailable on insecure origins, which is
// exactly where the hub lives (http://127.0.0.1:<port>) on some
// browser/OS combinations — so the ``execCommand`` path is the one
// that actually runs for a chunk of users, not dead weight.

/**
 * Copy ``text`` to the clipboard. Never throws; resolves false when
 * both paths are unavailable so the caller can skip the "copied"
 * flash rather than lie about it.
 *
 * @returns {Promise<boolean>}
 */
export async function copyText(text) {
  const value = typeof text === 'string' ? text : String(text ?? '')
  if (!value) return false
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch {
    /* permission denied / insecure origin — try the legacy path */
  }
  try {
    if (typeof document === 'undefined' || !document.body) return false
    const ta = document.createElement('textarea')
    ta.value = value
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand ? document.execCommand('copy') : false
    ta.remove()
    return ok === true
  } catch {
    return false
  }
}
