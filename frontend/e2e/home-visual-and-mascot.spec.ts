import { expect, test } from '@playwright/test'
import type { Locator } from '@playwright/test'
import { installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 1180 })
  await page.addInitScript((token) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', token)
  }, syntheticToken)
})

async function expectBox(
  locator: Locator,
  expected: { x: number; y: number; width: number; height: number },
) {
  const box = await locator.boundingBox()
  expect(box).not.toBeNull()
  expect(box?.x).toBeCloseTo(expected.x, 0)
  expect(box?.y).toBeCloseTo(expected.y, 0)
  expect(box?.width).toBeCloseTo(expected.width, 0)
  expect(box?.height).toBeCloseTo(expected.height, 0)
}

test('390px 미처방 HOME은 승인 시안의 Hero와 등록 카드 밀도를 유지한다', async ({ page }) => {
  await installRequirementsApi(page)
  await page.goto('/')

  await expect(page.getByRole('heading', { name: '오늘도 건강한 하루 되세요' })).toBeVisible()
  await expectBox(page.locator('.mvp-home__hero'), {
    x: 12,
    y: 88,
    width: 366,
    height: 190,
  })
  await expectBox(page.locator('.mvp-home__hub-card--prescription'), {
    x: 20,
    y: 356,
    width: 350,
    height: 176,
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
  await expect(page.getByRole('button', { name: /일반의약품 안내/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /도지에게 질문하기/ })).toHaveCount(0)
  await page.screenshot({ path: 'test-results/requirements/home-empty-390x1180.png' })
})

test('390px 처방 HOME은 승인 시안의 카드 간격과 마스코트 drag 방지를 유지한다', async ({ page }) => {
  await installRequirementsApi(page, { existingPrescription: true })
  await page.goto('/')

  await expect(page.getByRole('heading', { name: '이번 주 복약 달성도' })).toBeVisible()
  await expectBox(page.locator('.mvp-home__hero'), {
    x: 12,
    y: 88,
    width: 366,
    height: 190,
  })
  await expectBox(page.locator('.mvp-home__adherence-card'), {
    x: 20,
    y: 326,
    width: 350,
    height: 240,
  })
  await expectBox(page.locator('.mvp-home__hub-card--report'), {
    x: 20,
    y: 626,
    width: 350,
    height: 128,
  })

  const nav = page.getByRole('navigation', { name: '주요 메뉴' })
  const reportCard = page.locator('.mvp-home__hub-card--report')
  const navBox = await nav.boundingBox()
  const reportBox = await reportCard.boundingBox()
  expect(navBox).not.toBeNull()
  expect(reportBox).not.toBeNull()
  expect((navBox?.y ?? 0) - ((reportBox?.y ?? 0) + (reportBox?.height ?? 0))).toBeGreaterThanOrEqual(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)

  await page.evaluate(() => {
    const state = window as typeof window & { __doseyDragStarts?: number }
    state.__doseyDragStarts = 0
    document.addEventListener('dragstart', (event) => {
      if ((event.target as Element | null)?.matches('.dosey-mascot img')) {
        state.__doseyDragStarts = (state.__doseyDragStarts ?? 0) + 1
      }
    })
  })

  const mascotImages = page.locator('.dosey-mascot img')
  expect(await mascotImages.count()).toBeGreaterThanOrEqual(4)
  for (let index = 0; index < await mascotImages.count(); index += 1) {
    const image = mascotImages.nth(index)
    await expect(image).toHaveAttribute('draggable', 'false')
    const box = await image.boundingBox()
    if (!box) continue
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.down()
    await page.mouse.move(box.x + box.width / 2 + 30, box.y + box.height / 2 + 20, { steps: 4 })
    await page.mouse.up()
  }
  expect(await page.evaluate(() => (
    window as typeof window & { __doseyDragStarts?: number }
  ).__doseyDragStarts)).toBe(0)

  await page.screenshot({ path: 'test-results/requirements/home-active-390x1180.png' })

  await page.getByRole('button', { name: '도지' }).click()
  await expect(page).toHaveURL(/\/chat/)
  await expect(page.getByText(/먼저 처방전을 등록해 주세요|무엇을 도와드릴까요/)).toBeVisible()
})

test('390x844 처방 HOME은 스크롤 끝에서도 마지막 카드와 Bottom Navigation이 겹치지 않는다', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installRequirementsApi(page, { existingPrescription: true })
  await page.goto('/')

  const content = page.locator('.app-scroll')
  const reportCard = page.locator('.mvp-home__hub-card--report')
  const nav = page.getByRole('navigation', { name: '주요 메뉴' })
  await expect(reportCard).toBeVisible()
  await content.evaluate((element) => element.scrollTo(0, element.scrollHeight))

  const reportBox = await reportCard.boundingBox()
  const navBox = await nav.boundingBox()
  expect(reportBox).not.toBeNull()
  expect(navBox).not.toBeNull()
  expect((navBox?.y ?? 0) - ((reportBox?.y ?? 0) + (reportBox?.height ?? 0))).toBeGreaterThanOrEqual(0)
})

test('360px 미처방 HOME은 등록 카드 아이콘과 문구가 겹치지 않는다', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 844 })
  await installRequirementsApi(page)
  await page.goto('/')

  const iconBox = await page.locator('.mvp-home__hub-icon').boundingBox()
  const copyBox = await page.locator('.mvp-home__hub-copy').boundingBox()
  expect(iconBox).not.toBeNull()
  expect(copyBox).not.toBeNull()
  expect((copyBox?.x ?? 0) - ((iconBox?.x ?? 0) + (iconBox?.width ?? 0))).toBeGreaterThanOrEqual(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
})
