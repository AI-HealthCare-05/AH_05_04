import { expect, test, type Locator, type Page } from '@playwright/test'
import { installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'
import {
  installNotificationApi,
  notificationIds,
} from './fixtures/notificationApi'

const widths = [320, 390, 412] as const

async function authenticate(page: Page, width = 390) {
  await page.setViewportSize({ width, height: 844 })
  await page.addInitScript((token) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', token)
  }, syntheticToken)
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const app = document.querySelector<HTMLElement>('.mobile-app')
    return {
      document: document.documentElement.scrollWidth - window.innerWidth,
      app: app ? app.scrollWidth - app.clientWidth : null,
    }
  })
  expect(overflow.document).toBeLessThanOrEqual(0)
  expect(overflow.app).toBe(0)
}

async function expectHittableAboveNavigation(locator: Locator) {
  await locator.scrollIntoViewIfNeeded()
  const bounds = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect()
    const hit = document.elementFromPoint(
      rect.left + rect.width / 2,
      rect.top + rect.height / 2,
    )
    const navigation = document.querySelector('.bottom-nav')?.getBoundingClientRect()
    return {
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      viewportWidth: window.innerWidth,
      navigationTop: navigation?.top ?? window.innerHeight,
      hittable: hit === element || Boolean(hit && element.contains(hit)),
    }
  })
  expect(bounds.left).toBeGreaterThanOrEqual(0)
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewportWidth)
  expect(bounds.top).toBeGreaterThanOrEqual(0)
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.navigationTop)
  expect(bounds.hittable).toBe(true)
}

async function tabTo(page: Page, locator: Locator) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await page.keyboard.press('Tab')
    if (await locator.evaluate((element) => element === document.activeElement)) return
  }
  throw new Error('Notification was not reachable with keyboard Tab navigation.')
}

for (const width of widths) {
  test(`[Notification closeout][${width}px] unread keyboard handoff is responsive and mutation-free`, async ({ page }) => {
    await authenticate(page, width)
    const requirements = await installRequirementsApi(page)
    const api = await installNotificationApi(page)

    await page.goto('/notifications')
    const notification = page.getByRole('button', {
      name: '복약 재알림, 복약일 2026-09-10, 읽지 않음',
    })
    await expect(notification).toBeVisible()
    await expectNoHorizontalOverflow(page)
    await expectHittableAboveNavigation(notification)

    await tabTo(page, notification)
    await expect(notification).toBeFocused()
    const focus = await notification.evaluate((element) => {
      const style = getComputedStyle(element)
      return {
        style: style.outlineStyle,
        width: Number.parseFloat(style.outlineWidth),
      }
    })
    expect(focus.style).not.toBe('none')
    expect(focus.width).toBeGreaterThanOrEqual(3)

    await page.keyboard.press(width === 390 ? 'Space' : 'Enter')
    await expect(page).toHaveURL(
      new RegExp(`/schedule/occurrences/${notificationIds.unreadOccurrenceId}\\?date=2026-09-10$`),
    )
    const heading = page.getByRole('heading', { name: '복약 기록' })
    await expect(heading).toBeFocused()
    await expect(page.getByRole('heading', { name: '합성 과거 처방약' })).toBeVisible()
    await expectNoHorizontalOverflow(page)
    await expectHittableAboveNavigation(
      page.getByRole('button', { name: '복용했어요' }),
    )

    expect(api.readPatchCount).toBe(1)
    expect(api.lastReadBody).toEqual({})
    expect(api.lastReadIdempotencyKey).toMatch(/^notification-read:/)
    expect(api.occurrenceDayDates).toContain('2026-09-10')
    expect(api.occurrenceMedicationIds).toContain(notificationIds.unreadOccurrenceId)
    expect(api.checkinMutationCount).toBe(0)
    expect(requirements.unexpectedRequests).toEqual([])
  })
}

test('[Notification closeout] already-read notification skips PATCH and keeps the historical handoff', async ({ page }) => {
  await authenticate(page)
  const requirements = await installRequirementsApi(page)
  const api = await installNotificationApi(page)

  await page.goto('/notifications')
  const notification = page.getByRole('button', {
    name: '복약 알림, 복약일 2026-09-09, 읽음',
  })
  await tabTo(page, notification)
  await page.keyboard.press('Enter')

  await expect(page).toHaveURL(
    new RegExp(`/schedule/occurrences/${notificationIds.readOccurrenceId}\\?date=2026-09-09$`),
  )
  await expect(page.getByRole('heading', { name: '복약 기록' })).toBeFocused()
  expect(api.readPatchCount).toBe(0)
  expect(api.occurrenceDayDates).toContain('2026-09-09')
  expect(api.occurrenceMedicationIds).toContain(notificationIds.readOccurrenceId)
  expect(api.checkinMutationCount).toBe(0)
  expect(requirements.unexpectedRequests).toEqual([])
})

test('[Notification closeout] identity mismatch stays neutral without fallback or navigation', async ({ page }) => {
  await authenticate(page)
  const requirements = await installRequirementsApi(page)
  const api = await installNotificationApi(page, {
    deferNotificationList: true,
    identityFailure: true,
  })

  await page.goto('/notifications')
  const loading = page.getByRole('status').filter({ hasText: '알림을 불러오는 중이에요' })
  await expect(loading).toHaveAttribute('aria-live', 'polite')
  api.releaseNotificationList()

  await page.getByRole('button', {
    name: '복약 알림, 복약일 2026-09-09, 읽음',
  }).click()
  const alert = page.getByRole('alert').filter({ hasText: '복약 기록을 확인할 수 없어요.' })
  await expect(alert).toBeVisible()
  await expect(page).toHaveURL(/\/notifications$/)
  expect(api.readPatchCount).toBe(0)
  expect(api.currentPrescriptionGetCount).toBe(0)
  expect(api.checkinMutationCount).toBe(0)
  expect(requirements.unexpectedRequests).toEqual([])
})

test('[Notification closeout][#551] leaving a pending handoff blocks late navigation', async ({ page }) => {
  await authenticate(page)
  const requirements = await installRequirementsApi(page)
  const api = await installNotificationApi(page, { deferOccurrenceDay: true })

  await page.goto('/notifications')
  await page.getByRole('button', {
    name: '복약 재알림, 복약일 2026-09-10, 읽지 않음',
  }).click()
  await api.waitForOccurrenceDayRequest()
  expect(api.readPatchCount).toBe(1)

  await page.getByRole('button', { name: '이전 화면' }).click()
  await expect(page).toHaveURL(/\/$/)
  api.releaseOccurrenceDay()
  await api.waitForOccurrenceDayRelease()

  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByText('오늘도 건강한 하루 되세요')).toBeVisible()
  expect(api.occurrenceMedicationIds).toEqual([])
  expect(api.checkinMutationCount).toBe(0)
  expect(requirements.unexpectedRequests).toEqual([])
})
