// App-level keyboard shortcuts.
//
// Factored out of App.vue so the behaviour is unit-testable against a
// plain KeyboardEvent without mounting the whole shell. App.vue owns
// the register/unregister lifecycle.
//
// Shortcuts (discoverable from the NodeDetail button titles):
//   Esc — dismiss the disambiguation picker if it is open, otherwise
//         clear the selection. One key, two steps, most-recent-first.
//   u   — ascend one level. No-op unless a scope is set, so the key
//         is inert on a design sitting at its top.
//   /   — focus the hierarchy filter box. The one key every tree-based
//         tool (and every browser) binds to "search this list"; the
//         placeholder advertises it.
//
// Deliberately NOT handled: anything with a modifier (that is a
// browser/OS shortcut), and anything typed into a form control — the
// model/test pickers are ``<select>`` elements where ``u`` is
// type-ahead.

const FORM_TAGS = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'OPTION'])

/** True when the event came from somewhere the user is typing. */
export function isTypingTarget(target) {
  if (!target || typeof target !== 'object') return false
  const tag = typeof target.tagName === 'string' ? target.tagName.toUpperCase() : ''
  if (FORM_TAGS.has(tag)) return true
  return target.isContentEditable === true
}

/**
 * Build the document-level keydown handler.
 *
 * ``focusTreeFilter`` is an optional callback supplied by App.vue that
 * focuses the hierarchy panel's filter input and returns false when it
 * could not (panel collapsed, tree not mounted) — in which case the
 * keystroke is left to the browser rather than swallowed.
 *
 * @param {{store: object, hub: object, focusTreeFilter?: () => boolean}} deps
 * @returns {(event: KeyboardEvent) => void}
 */
export function makeGlobalKeydownHandler({ store, focusTreeFilter }) {
  return function onGlobalKeydown(event) {
    if (!event || event.defaultPrevented) return
    if (event.ctrlKey || event.metaKey || event.altKey) return
    if (isTypingTarget(event.target)) return

    if (event.key === 'Escape') {
      const candidates = store?.selectionCandidates
      if (Array.isArray(candidates) && candidates.length > 0) {
        store?.dismissSelectionCandidates?.()
      } else {
        store?.clearSelection?.()
      }
      event.preventDefault()
      return
    }

    if (event.key === '/') {
      // Esc inside that input is the tree's to handle (clear the
      // filter); it never reaches here because ``isTypingTarget``
      // already excluded form controls above.
      if (typeof focusTreeFilter === 'function' && focusTreeFilter() !== false) {
        event.preventDefault()
      }
      return
    }

    if (event.key === 'u' || event.key === 'U') {
      if (store?.rootInstancePath || store?.flowScope) {
        store.ascend()
        event.preventDefault()
      }
    }
  }
}
