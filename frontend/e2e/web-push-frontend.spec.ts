import { expect, test } from '@playwright/test'
import { installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'

for (const width of [320, 390, 412]) {
  test(`[Web Push #470][${width}px] 설정 화면은 자동 prompt 없이 keyboard 접근과 fallback을 유지한다`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await page.addInitScript((token) => {
      localStorage.clear()
      sessionStorage.clear()
      localStorage.setItem('access_token', token)
      Object.defineProperty(window, '__doseyPermissionRequestCount', {
        configurable: true,
        writable: true,
        value: 0,
      })
      Object.defineProperty(Notification, 'requestPermission', {
        configurable: true,
        value: async () => {
          const state = window as typeof window & { __doseyPermissionRequestCount: number }
          state.__doseyPermissionRequestCount += 1
          return 'denied' as NotificationPermission
        },
      })
      Object.defineProperty(Notification, 'permission', {
        configurable: true,
        value: 'default',
      })
    }, syntheticToken)
    const api = await installRequirementsApi(page)

    await page.goto('/settings/notifications')
    const enable = page.getByRole('button', { name: '알림 켜기' })
    await expect(enable).toBeVisible()
    await expect(page.getByRole('button', { name: '앱 안의 알림 목록 보기' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0)
    expect(await page.evaluate(() => (window as typeof window & { __doseyPermissionRequestCount: number }).__doseyPermissionRequestCount)).toBe(0)

    for (let attempt = 0; attempt < 20 && !(await enable.evaluate((element) => element === document.activeElement)); attempt += 1) {
      await page.keyboard.press('Tab')
    }
    await expect(enable).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByText('브라우저에서 알림이 차단되어 있어요')).toBeVisible()
    expect(await page.evaluate(() => (window as typeof window & { __doseyPermissionRequestCount: number }).__doseyPermissionRequestCount)).toBe(1)
    expect(api.unexpectedRequests).toEqual([])
  })
}
