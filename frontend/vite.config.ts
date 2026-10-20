import { cpSync } from 'node:fs'
import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

const demo = process.env.VITE_DEMO === '1'

/** A demo build ships the gold-derived responses; an ordinary build does not. */
function demoData(): Plugin {
  return {
    name: 'demo-data',
    apply: 'build',
    closeBundle() {
      cpSync('demo-data/demo', 'dist/demo', { recursive: true })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  // A site served under a subpath (GitHub Pages serves /<repo>/) sets VITE_BASE;
  // the router and the demo's data fetches read it back as BASE_URL.
  base: process.env.VITE_BASE || '/',
  plugins: [react(), ...(demo ? [demoData()] : [])],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
})
