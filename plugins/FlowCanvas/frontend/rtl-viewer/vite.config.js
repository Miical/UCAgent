import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '',
  plugins: [vue()],
  build: {
    outDir: 'dist',
    sourcemap: true,
    // Graphviz WASM and ELK are bundled for the embedded viewer.
    chunkSizeWarningLimit: 3500,
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
})
