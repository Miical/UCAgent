// Viewer entry. Wires Vue 3 + Pinia, mounts App, and hands off to
// the store for view.json loading. The store's source-of-truth
// inputs are: ?view= URL query param (highest priority), the
// `window.__RTL_BUDDY_VIEW_DATA__` injection point (for embed.py
// self-contained HTML), drag-and-drop, and the file picker.
//
// Stylesheet order is load-bearing: the vendored hub token sheet
// first (it defines every ``--token`` and the light/dark blocks),
// then the SPA's own tokens, then the global element + utility
// styles that consume them. Component <style> blocks land after all
// three regardless of import order.
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './theme.css'
import './tokens.css'
import './app.css'
import App from './App.vue'
import { useViewerStore } from './store.js'
import { applyStoredThemePreference } from './theme.js'

// Before mount: a remembered ``data-theme`` pin has to be on <html>
// for the first paint, or the page flashes the OS palette first.
applyStoredThemePreference()

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.mount('#app')

// Kick off the initial load on next tick so the store is wired
// through Pinia before the loader writes into it.
queueMicrotask(() => {
  const store = useViewerStore()
  store.bootstrap()
})
