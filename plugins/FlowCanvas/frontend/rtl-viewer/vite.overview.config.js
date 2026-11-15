/** Bundle the reusable overview renderer with the existing Graphviz WASM engine. */
import { defineConfig } from 'vite'
export default defineConfig({build:{outDir:'dist',emptyOutDir:false,chunkSizeWarningLimit:3500,lib:{entry:'src/overview-structure.js',formats:['es'],fileName:()=> 'overview-structure.js'},rollupOptions:{output:{inlineDynamicImports:true}}}})
