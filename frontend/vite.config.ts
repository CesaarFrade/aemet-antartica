import react from '@vitejs/plugin-react'
// `defineConfig` comes from `vitest/config`, not from `vite`: the `test` block below
// is a Vitest option that Vite's own `UserConfig` type does not know about, so
// importing it from `vite` fails the `tsc -b` step of `npm run build`.
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  envDir: '../',
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.ts'],
  }
})
