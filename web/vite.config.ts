import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __VUE_OPTIONS_API__: false, __VUE_PROD_DEVTOOLS__: false, __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: false },
  server: { proxy: { '/api': 'http://127.0.0.1:5180' } },
  preview: { proxy: { '/api': 'http://127.0.0.1:5180' } },
  build: {
    chunkSizeWarningLimit: 650,
    // The production CSP only allows same-origin fonts.
    assetsInlineLimit: file => /\.(?:woff2?|ttf)$/.test(file) ? false : undefined,
  },
})
