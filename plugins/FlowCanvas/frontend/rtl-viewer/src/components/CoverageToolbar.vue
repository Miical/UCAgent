<template>
  <div class="coverage-toolbar">
    <label><input type="radio" name="coverage-display" value="none" v-model="store.projectCoverageMode">无</label>
    <label><input type="radio" name="coverage-display" value="line" v-model="store.projectCoverageMode">行覆盖率</label>
    <label><input type="radio" name="coverage-display" value="functional" v-model="store.projectCoverageMode">功能覆盖率</label>
    <span v-if="store.projectCoverageError" :title="store.projectCoverageError">数据读取失败</span>
  </div>
</template>
<script setup>
import { useViewerStore } from '../store.js'
const store = useViewerStore()
</script>
<style scoped>
.coverage-toolbar { position:absolute; bottom:32px; left:0; right:0; height:28px; box-sizing:border-box; padding:4px 10px; background:var(--panel); border-top:1px solid var(--line); z-index:10; display:flex;gap:14px;align-items:center;font-size:11px; color:var(--fg-muted); }
label { display:inline-flex; align-items:center; gap:6px; cursor:pointer; }
input { margin:0; width:12px; height:12px; accent-color:#409b73; }
</style>
