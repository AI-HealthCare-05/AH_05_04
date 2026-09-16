import { expect, test, type Page } from '@playwright/test'
import { ids, installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'
import { installNotificationApi } from './fixtures/notificationApi'

async function expectContentFits(page: Page) {
  const content = page.locator('.app-scroll')
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
  expect(await content.evaluate((element) => element.scrollWidth - element.clientWidth)).toBe(0)
  // Check descendants too: overflow-x:hidden must not conceal clipped content.
  const clipped = await content.evaluate((element) => {
    const container = element.getBoundingClientRect()
    return [...element.querySelectorAll('button, input, h2, h3, strong, small, dt, dd')]
      .filter((child) => {
        const box = child.getBoundingClientRect()
        return box.width > 0 && (box.left < container.left - 1 || box.right > container.right + 1)
      }).map((child) => child.textContent)
  })
  expect(clipped).toEqual([])
  const navigation = page.getByRole('navigation', { name: '주요 메뉴' })
  if (await navigation.count()) {
    await content.evaluate((element) => element.scrollTo(0, element.scrollHeight))
    const last = await content.locator(':scope > *').last().boundingBox()
    const nav = await navigation.boundingBox()
    expect(last!.y + last!.height).toBeLessThanOrEqual(nav!.y)
  }
  await content.evaluate((element) => element.scrollTo(0, 0))
}

for (const width of [320, 390, 412]) {
  test(`[Figma #645][${width}px] 시작·메뉴·사용자 정보·알림의 실제 콘텐츠와 모바일 배치`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await page.clock.setFixedTime(new Date('2026-09-11T01:00:00Z'))
    await page.addInitScript((token) => {
      localStorage.clear()
      sessionStorage.clear()
      if (location.pathname !== '/start') localStorage.setItem('access_token', token)
      document.documentElement.style.setProperty('--ds-safe-bottom', '34px')
    }, syntheticToken)
    const api = await installRequirementsApi(page)
    const notifications = await installNotificationApi(page)

    await page.goto('/start')
    await expect(page.getByRole('button', { name: '회원가입하고 시작하기' })).toBeVisible()
    await expectContentFits(page)
    await page.screenshot({ path: `test-results/requirements/figma-645-start-${width}.png` })

    await page.goto('/menu')
    await expect(page.getByRole('button', { name: '복약 일정', exact: true })).toBeVisible()
    for (const icon of await page.locator('.mvp-menu__icon img').all()) {
      await expect.poll(() => icon.evaluate((element) => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)
    }
    await expectContentFits(page)
    await page.screenshot({ path: `test-results/requirements/figma-645-menu-${width}.png` })
    await page.getByRole('button', { name: '사용자 정보', exact: true }).click()
    await expect(page.getByText('synthetic@example.com')).toBeVisible()
    await expectContentFits(page)
    await page.screenshot({ path: `test-results/requirements/figma-645-profile-${width}.png` })

    // Long user-supplied text must wrap without pushing badges or actions off-screen.
    await page.route('**/api/v1/users/me', (route) => route.fulfill({ json: {
      id: ids.user, name: '합성사용자이름이긴경우레이아웃확인',
      email: 'synthetic.long.account.name.for.mobile@example.com',
      phone_number: null, birthday: null, gender: null, created_at: '2026-09-11T00:00:00Z',
    } }))
    await page.reload()
    await expect(page.getByText('synthetic.long.account.name.for.mobile@example.com')).toBeVisible()
    await expectContentFits(page)
    await page.getByRole('button', { name: '이름·이메일 수정' }).click()
    await expect(page.getByLabel('이름')).toBeVisible()
    await page.setViewportSize({ width, height: 480 })
    await expectContentFits(page)
    await page.getByRole('button', { name: '취소', exact: true }).click()
    await page.setViewportSize({ width, height: 844 })

    await page.goto('/notifications')
    await expect(page.getByRole('region', { name: '오늘', exact: true })).toBeVisible()
    await expect(page.getByRole('region', { name: '이전 알림', exact: true })).toBeVisible()
    const unread = page.locator('.mvp-notifications__item-button.is-unread')
    const read = page.locator('.mvp-notifications__item-button.is-read')
    await expect(unread.locator('.mvp-notifications__read-state')).toBeVisible()
    await expect(read.locator('.mvp-notifications__read-state')).toBeVisible()
    const parts = await unread.locator(':scope > span').evaluateAll((elements) => elements.map((element) => ({
      text: element.textContent, y: element.getBoundingClientRect().y,
    })))
    expect(parts[0].text).toContain('새 알림')
    expect(parts[1].text).toContain('복약일 2026-09-10')
    expect(parts[2].text).toContain('복용 여부 기록하기')
    expect(parts[0].y).toBeLessThan(parts[1].y)
    expect(parts[1].y).toBeLessThan(parts[2].y)
    await expectContentFits(page)
    await page.screenshot({ path: `test-results/requirements/figma-645-notifications-${width}.png` })
    expect(notifications.readPatchCount).toBe(0)
    expect(notifications.checkinMutationCount).toBe(0)
    expect(api.profilePatchCount).toBe(0)
    expect(api.unexpectedRequests).toEqual([])
  })
}
