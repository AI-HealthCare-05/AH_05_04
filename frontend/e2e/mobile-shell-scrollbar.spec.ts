import { expect, test } from '@playwright/test'
import { installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'

const mobileShellRoutes = [
  { name: 'Signup', path: '/signup' },
  { name: 'Home', path: '/' },
  { name: 'Prescription Upload', path: '/prescriptions/upload' },
  { name: 'Guide', path: '/guides' },
  { name: 'Chat', path: '/chat' },
  { name: 'Menu', path: '/menu' },
] as const

test('MobileShell 공통 스크롤바는 숨기고 wheel·keyboard 스크롤은 유지한다', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 480 })
  await page.addInitScript((token) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', token)
  }, syntheticToken)
  await installRequirementsApi(page)

  for (const route of mobileShellRoutes) {
    await test.step(route.name, async () => {
      await page.goto(route.path)
      const scroller = page.locator('.app-scroll').first()
      await expect(scroller).toBeVisible()

      const styles = await scroller.evaluate((element) => ({
        overflowY: getComputedStyle(element).overflowY,
        scrollbarWidth: getComputedStyle(element).scrollbarWidth,
        webkitDisplay: getComputedStyle(element, '::-webkit-scrollbar').display,
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
      }))
      expect(styles.overflowY).toBe('auto')
      expect(styles.scrollbarWidth).toBe('none')
      expect(styles.webkitDisplay).toBe('none')
      expect(styles.scrollHeight, `${route.name} should overflow at the compact viewport`).toBeGreaterThan(styles.clientHeight)

      const box = await scroller.boundingBox()
      expect(box).not.toBeNull()
      await page.mouse.move((box?.x ?? 0) + 10, (box?.y ?? 0) + 10)
      await page.mouse.wheel(0, 240)
      await expect.poll(() => scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)

      await scroller.evaluate((element) => {
        element.scrollTop = 0
        const focusable = element as HTMLElement
        focusable.tabIndex = -1
        focusable.focus()
      })
      await page.keyboard.press('PageDown')
      await expect.poll(() => scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
    })
  }
})
