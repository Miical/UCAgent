<template>
  <div class="coverage-toolbar">
    <label><input type="checkbox" v-model="store.projectCoverageEnabled">行覆盖率</label>
  </div>
</template>
<script setup>
import { useViewerStore } from '../store.js'
const store = useViewerStore()
</script>
<style scoped>
.coverage-toolbar { position:absolute; bottom:32px; left:0; right:0; height:28px; box-sizing:border-box; padding:4px 10px; background:var(--panel); border-top:1px solid var(--line); z-index:10; font-size:11px; color:var(--fg-muted); }
label { display:inline-flex; align-items:center; gap:6px; cursor:pointer; }
input { margin:0; width:12px; height:12px; accent-color:#409b73; }
</style>
