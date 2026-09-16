import { expect, test, type Page } from '@playwright/test'
import {
  ids,
  installRequirementsApi,
  syntheticToken,
} from './fixtures/requirementsApi'

const mobileWidths = [320, 390, 412] as const
const withdrawnTitle = '동의가 철회되어 처리를 계속할 수 없어요.'
const consentHelper = '동의 설정을 확인한 뒤 다시 이용해 주세요.'

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', token)
    document.documentElement.style.setProperty('--ds-safe-bottom', '34px')
  }, syntheticToken)
})

async function assertMobileErrorLayout(
  page: Page,
  contentSelector: string,
) {
  const content = page.locator(contentSelector)
  const heading = page.getByRole('heading', { name: withdrawnTitle })
  const helper = page.getByText(consentHelper)
  const action = page.getByRole('button', { name: '동의 설정 확인하기' })
  const navigation = page.getByRole('navigation', { name: '주요 메뉴' })

  await expect(heading).toBeVisible()
  await expect(helper).toBeVisible()
  await action.scrollIntoViewIfNeeded()
  await expect(action).toBeVisible()

  const helperMetrics = await helper.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }))
  expect(helperMetrics.scrollHeight).toBeLessThanOrEqual(helperMetrics.clientHeight)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
  expect(await content.evaluate((element) => element.scrollWidth - element.clientWidth)).toBe(0)

  const actionBox = await action.boundingBox()
  const navigationBox = await navigation.boundingBox()
  expect(actionBox).not.toBeNull()
  expect(navigationBox).not.toBeNull()
  expect(actionBox?.x ?? -1).toBeGreaterThanOrEqual(0)
  expect((actionBox?.x ?? 0) + (actionBox?.width ?? 0)).toBeLessThanOrEqual(
    await page.evaluate(() => innerWidth),
  )
  expect((navigationBox?.y ?? 0) - ((actionBox?.y ?? 0) + (actionBox?.height ?? 0))).toBeGreaterThanOrEqual(0)
}

test('Chat 동의 철회 alert는 320/390/412px에서 문구·helper·CTA를 안전하게 표시한다', async ({ page }) => {
  await installRequirementsApi(page, {
    existingPrescription: true,
    existingChat: true,
  })
  await page.route(
    `**/api/v1/prescriptions/${ids.prescription}/chat-session`,
    (route) => route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'CONSENT_WITHDRAWN',
        message: '노출하면 안 되는 Backend 메시지',
        details: [],
        trace_id: 'synthetic-chat-consent-withdrawn',
      }),
    }),
  )

  for (const width of mobileWidths) {
    await test.step(`${width}px Chat`, async () => {
      await page.setViewportSize({ width, height: 844 })
      await page.goto(`/chat?prescription_id=${ids.prescription}`)
      await assertMobileErrorLayout(page, '.chat-messages')
      await expect(page.getByText('노출하면 안 되는 Backend 메시지')).toHaveCount(0)
    })
  }
})

test('Guide 동의 철회 alert는 320/390/412px에서 문구·helper·CTA를 안전하게 표시한다', async ({ page }) => {
  await installRequirementsApi(page, {
    existingPrescription: true,
  })
  await page.route(`**/api/v1/guides/${ids.guide}`, (route) => route.fulfill({
    status: 403,
    contentType: 'application/json',
    body: JSON.stringify({
      code: 'CONSENT_WITHDRAWN',
      message: '노출하면 안 되는 Backend 메시지',
      details: [],
      trace_id: 'synthetic-guide-consent-withdrawn',
    }),
  }))

  for (const width of mobileWidths) {
    await test.step(`${width}px Guide`, async () => {
      await page.setViewportSize({ width, height: 844 })
      await page.goto(`/guides/${ids.guide}`)
      await assertMobileErrorLayout(page, '.guide-page__content')
      await expect(page.getByText('노출하면 안 되는 Backend 메시지')).toHaveCount(0)
    })
  }
})
