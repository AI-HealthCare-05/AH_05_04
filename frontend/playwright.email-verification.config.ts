import { defineConfig } from '@playwright/test'
import base from './playwright.config'

// Explicit opt-in exercises #507 without enabling the unreleased flow elsewhere.
export default defineConfig({
  ...base,
  testIgnore: [],
  testMatch: '**/signup-email-verification.spec.ts',
  outputDir: './test-results/requirements/email-verification',
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report/requirements/email-verification', open: 'never' }],
  ],
  use: { ...base.use, baseURL: 'http://127.0.0.1:4174' },
  webServer: {
    command: 'pnpm dev --host 127.0.0.1 --port 4174',
    url: 'http://127.0.0.1:4174',
    reuseExistingServer: false,
    env: {
      VITE_API_BASE_URL: 'http://127.0.0.1:4174',
      VITE_EMAIL_VERIFICATION_ENABLED: 'true',
    },
  },
})
