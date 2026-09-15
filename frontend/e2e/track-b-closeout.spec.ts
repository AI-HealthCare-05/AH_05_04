import { expect, test, type Locator, type Page } from '@playwright/test'
import { installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'
import {
  installTrackBApi,
  longHistoricalMedicationName,
  trackBIds,
} from './fixtures/trackBApi'

const widths = [320, 390, 412] as const

async function setAuthenticatedViewport(page: Page, width: number) {
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

async function expectInsideViewport(locator: Locator) {
  await locator.evaluate((element) => {
    element.scrollIntoView({ block: 'center', inline: 'nearest' })
  })
  const result = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect()
    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2
    const hit = document.elementFromPoint(centerX, centerY)
    return {
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      width: window.innerWidth,
      height: window.innerHeight,
      hittable: hit === element || Boolean(hit && element.contains(hit)),
    }
  })
  expect(result.left).toBeGreaterThanOrEqual(0)
  expect(result.right).toBeLessThanOrEqual(result.width)
  expect(result.top).toBeGreaterThanOrEqual(0)
  expect(result.bottom).toBeLessThanOrEqual(result.height)
  expect(result.hittable).toBe(true)
}

for (const width of widths) {
  test(`[Track B closeout][${width}px] historical backlog is responsive and keyboard operable`, async ({ page }) => {
    await setAuthenticatedViewport(page, width)
    await installRequirementsApi(page)
    const api = await installTrackBApi(page)

    await page.goto('/schedule/unconfirmed')
    const card = page.locator('.unconfirmed-card')
    const taken = page.getByRole('button', { name: /복용했어요/ })
    const notTaken = page.getByRole('button', { name: /복용하지 않았어요/ })
    const later = page.getByRole('button', { name: '지금은 확인하기 어려워요' })

    await expect(card).toBeVisible()
    await expect(page.getByText(longHistoricalMedicationName, { exact: false })).toBeVisible()
    await expect(page.getByText(/9월 4일/)).toBeVisible()
    await expect(page.getByText(/13:00/)).toBeVisible()
    await expect(taken).toHaveAttribute('aria-pressed', 'false')
    await expect(notTaken).toHaveAttribute('aria-pressed', 'false')
    await expectNoHorizontalOverflow(page)

    const medicationBounds = await page.getByText(longHistoricalMedicationName, { exact: false })
      .evaluate((element) => {
        const text = element.getBoundingClientRect()
        const cardBounds = element.closest('.unconfirmed-card')?.getBoundingClientRect()
        return cardBounds && {
          left: text.left - cardBounds.left,
          right: cardBounds.right - text.right,
        }
      })
    expect(medicationBounds).not.toBeNull()
    expect(medicationBounds?.left).toBeGreaterThanOrEqual(0)
    expect(medicationBounds?.right).toBeGreaterThanOrEqual(0)

    const accessibilityOrder = await card.ariaSnapshot()
    const dateIndex = accessibilityOrder.indexOf('9월 4일')
    const medicationIndex = accessibilityOrder.indexOf(longHistoricalMedicationName)
    const questionIndex = accessibilityOrder.indexOf('실제로 어떻게 복용하셨나요?')
    expect(dateIndex).toBeGreaterThanOrEqual(0)
    expect(medicationIndex).toBeGreaterThan(dateIndex)
    expect(questionIndex).toBeGreaterThan(medicationIndex)

    await taken.focus()
    await expect(taken).toBeFocused()
    const outline = await taken.evaluate((element) => {
      const style = getComputedStyle(element)
      return { style: style.outlineStyle, width: Number.parseFloat(style.outlineWidth) }
    })
    expect(outline.style).not.toBe('none')
    expect(outline.width).toBeGreaterThanOrEqual(3)
    await page.keyboard.press('Tab')
    await expect(notTaken).toBeFocused()

    await expectInsideViewport(taken)
    await expectInsideViewport(notTaken)
    await expectInsideViewport(later)
    await later.focus()
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL((url) => url.pathname === '/schedule')
    expect(api.checkinPutCount).toBe(0)
  })

  test(`[Track B closeout][${width}px] loading, empty, error, and disabled states keep semantics`, async ({ page }) => {
    await setAuthenticatedViewport(page, width)
    await installRequirementsApi(page)
    const api = await installTrackBApi(page, { backlogMode: 'loading' })

    await page.goto('/schedule/unconfirmed')
    const loading = page.getByRole('status').filter({ hasText: '미확인 기록을 불러오는 중이에요' })
    await expect(loading).toHaveAttribute('aria-live', 'polite')
    await expectNoHorizontalOverflow(page)

    api.setBacklogMode('empty')
    api.releaseBacklog()
    const empty = page.getByRole('status').filter({ hasText: '확인할 미확인 기록이 없어요' })
    await expect(empty).toHaveAttribute('aria-live', 'polite')
    await expectNoHorizontalOverflow(page)

    api.setBacklogMode('error')
    await page.reload()
    const alert = page.getByRole('alert').filter({ hasText: '미확인 기록을 잠시 불러오지 못했어요' })
    await expect(alert).toHaveAttribute('aria-live', 'assertive')
    await expectInsideViewport(page.getByRole('button', { name: '다시 시도' }))
    await expectNoHorizontalOverflow(page)

    api.setBacklogMode('populated')
    api.setMedicationError(true)
    await page.reload()
    await expect(page.getByRole('alert').filter({ hasText: '원래 약 정보를 확인하지 못했어요' })).toBeVisible()
    await expect(page.getByRole('button', { name: /복용했어요/ })).toBeDisabled()
    await expect(page.getByRole('button', { name: /복용하지 않았어요/ })).toBeDisabled()
    await expectNoHorizontalOverflow(page)
    expect(api.checkinPutCount).toBe(0)
  })
}

test('[Track B closeout] login rediscovery uses GET and routes backlog to recovery', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installRequirementsApi(page)
  const api = await installTrackBApi(page)

  await page.goto('/login')
  await page.getByLabel('이메일', { exact: true }).fill('synthetic@example.com')
  await page.getByLabel('비밀번호').fill('Synthetic1!')
  await page.getByRole('button', { name: '로그인' }).click()

  await expect(page).toHaveURL(/\/schedule\/unconfirmed$/)
  await expect(page.getByText(longHistoricalMedicationName, { exact: false })).toBeVisible()
  expect(api.unconfirmedLimitOneGetCount).toBe(1)
  expect(api.unconfirmedGetCount).toBeGreaterThanOrEqual(2)
  expect(api.checkinPutCount).toBe(0)
})

test('[Track B closeout] occurrence initial failure retries the same historical identity without mutation', async ({ page }) => {
  await setAuthenticatedViewport(page, 390)
  await installRequirementsApi(page)
  const api = await installTrackBApi(page, { occurrenceDayMode: 'error' })

  await page.goto(`/schedule/occurrences/${trackBIds.occurrenceId}?date=2026-09-04`)
  const alert = page.getByRole('alert').filter({ hasText: '일정을 잠시 불러오지 못했어요' })
  await expect(alert).toHaveAttribute('aria-live', 'assertive')
  const initialGetCount = api.occurrenceDayGetCount
  api.setOccurrenceDayMode('success')
  await page.getByRole('button', { name: '다시 시도' }).click()

  await expect(page.getByRole('heading', { name: longHistoricalMedicationName })).toBeVisible()
  expect(api.occurrenceDayGetCount).toBeGreaterThan(initialGetCount)
  expect(api.occurrenceMedicationGetCount).toBeGreaterThanOrEqual(1)
  expect(api.checkinPutCount).toBe(0)
})
