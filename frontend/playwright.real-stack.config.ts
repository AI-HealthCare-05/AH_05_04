import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e-real-stack',
  outputDir: './test-results/real-stack',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 90_000 },
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report/real-stack', open: 'never' }],
  ],
  use: {
    baseURL: process.env.REAL_STACK_WEB_URL ?? 'http://127.0.0.1:14173',
    ...devices['Desktop Chrome'],
    viewport: { width: 390, height: 844 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [{ name: 'chromium-real-stack-ai-one-cycle' }],
})
