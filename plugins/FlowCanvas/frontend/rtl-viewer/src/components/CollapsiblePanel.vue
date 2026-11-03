<template>
  <section class="collapsible-panel" :data-collapsed="collapsed ? 'true' : 'false'">
    <button
      type="button"
      class="collapsible-header"
      @click="toggle"
      :aria-expanded="!collapsed"
      :title="collapsed ? `Show ${title}` : `Hide ${title}`"
    >
      <span class="chevron" aria-hidden="true">{{ collapsed ? '▸' : '▾' }}</span>
      <span class="title">{{ title }}</span>
      <span v-if="badge !== null && badge !== undefined" class="badge">{{ badge }}</span>
    </button>
    <div v-show="!collapsed" class="collapsible-body">
      <slot />
    </div>
  </section>
</template>

<script setup>
// Reusable collapsible-section wrapper for the sidebar panels.
// Collapsed state per-section is persisted in ``localStorage`` so
// the layout doesn't reset between page refreshes / hub restarts.
//
// Use ``persist-key`` (string) to namespace the localStorage entry
// — different sections need different keys so they don't fight
// over the same flag.
import { ref, watch, onMounted } from 'vue'

const props = defineProps({
  title: { type: String, required: true },
  persistKey: { type: String, required: true },
  defaultCollapsed: { type: Boolean, default: false },
  // Optional small count / indicator shown on the right of the
  // header strip. Pass ``null`` to hide.
  badge: { type: [String, Number, null], default: null },
})

const STORAGE_PREFIX = 'rb-viewer.panel-collapsed.'

function storageKey() {
  return STORAGE_PREFIX + props.persistKey
}

const collapsed = ref(props.defaultCollapsed)

onMounted(() => {
  try {
    const raw = window.localStorage.getItem(storageKey())
    if (raw === '1') collapsed.value = true
    else if (raw === '0') collapsed.value = false
  } catch {
    /* localStorage may be unavailable (private-mode, sandboxed) */
  }
})

watch(collapsed, (next) => {
  try {
    window.localStorage.setItem(storageKey(), next ? '1' : '0')
  } catch {
    /* see above */
  }
})

function toggle() {
  collapsed.value = !collapsed.value
}
</script>

<style scoped>
.collapsible-panel {
  border-top: 1px solid var(--line);
}
.collapsible-panel:first-child {
  border-top: 0;
}
.collapsible-header {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.5rem;
  background: transparent;
  border: 0;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: var(--fg);
}
.collapsible-header:hover {
  background: var(--bg);
}
.collapsible-header .chevron {
  font-size: 0.7rem;
  color: var(--fg-muted);
  width: 0.7rem;
  text-align: center;
}
.collapsible-header .title {
  flex: 1;
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.01em;
}
.collapsible-header .badge {
  font-size: 0.7rem;
  color: var(--fg-muted);
  background: var(--panel-2);
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
}
.collapsible-body {
  padding: 0 0.25rem 0.5rem;
}
</style>
