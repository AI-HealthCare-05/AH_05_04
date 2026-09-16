import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e-terms-review',
  outputDir: './test-results/terms-review',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:4175',
    ...devices['Desktop Chrome'],
    viewport: { width: 390, height: 844 },
  },
  webServer: {
    command: 'pnpm dev --host 127.0.0.1 --port 4175 --strictPort',
    url: 'http://127.0.0.1:4175',
    reuseExistingServer: false,
    env: {
      VITE_API_BASE_URL: 'http://127.0.0.1:4175',
      VITE_EMAIL_VERIFICATION_ENABLED: 'false',
      VITE_SIGNUP_TERMS_APPROVED: 'false',
    },
  },
})
