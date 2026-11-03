<template>
  <div class="no-view" role="status">
    <p>{{ store.error || '请选择项目以查看 RTL 结构' }}</p>
  </div>
</template>
<script setup>
import { useViewerStore } from '../store.js'
const store = useViewerStore()
</script>
<style scoped>
.no-view { padding: 24px; color: var(--fg-muted); overflow-wrap: anywhere; }
</style>
