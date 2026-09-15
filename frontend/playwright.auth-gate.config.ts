import { defineConfig, devices } from '@playwright/test'

if (process.env.SIGNUP_GATE_E2E !== '1') {
  throw new Error('Run bash scripts/e2e/signup_gate.sh to create the isolated Backend and database.')
}

export default defineConfig({
  testDir: './e2e-auth-gate',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: 'list',
  outputDir: './test-results/auth-gate',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://127.0.0.1:18433',
    viewport: { width: 390, height: 844 },
    // LOCAL request responses contain synthetic verification tokens. Do not record them.
    trace: 'off',
    video: 'off',
    screenshot: 'off',
  },
  webServer: {
    command: 'pnpm dev --host 127.0.0.1 --port 18433 --strictPort',
    url: 'http://127.0.0.1:18433',
    reuseExistingServer: false,
    env: {
      VITE_API_BASE_URL: 'http://127.0.0.1:18432',
      VITE_EMAIL_VERIFICATION_ENABLED: 'true',
    },
  },
})
