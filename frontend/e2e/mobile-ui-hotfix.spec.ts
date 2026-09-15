import { expect, test } from '@playwright/test'
import {
  ids,
  installRequirementsApi,
  syntheticToken,
} from './fixtures/requirementsApi'

const mobileWidths = [320, 390, 412] as const

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', token)
    document.documentElement.style.setProperty('--ds-safe-bottom', '34px')
  }, syntheticToken)
})

test('320/390/412px에서 Home과 Chat의 Bottom Navigation 안전 영역과 가용 공간을 유지한다', async ({ page }) => {
  await installRequirementsApi(page, {
    existingPrescription: true,
    existingChat: true,
  })

  for (const width of mobileWidths) {
    await test.step(`${width}px Home`, async () => {
      await page.setViewportSize({ width, height: 844 })
      await page.goto('/')

      const content = page.locator('.mvp-home-page .app-scroll')
      const reportCard = page.locator('.mvp-home__hub-card--report')
      const nav = page.getByRole('navigation', { name: '주요 메뉴' })
      await expect(reportCard).toBeVisible()
      await content.evaluate((element) => element.scrollTo(0, element.scrollHeight))

      const reportBox = await reportCard.boundingBox()
      const navBox = await nav.boundingBox()
      expect(reportBox).not.toBeNull()
      expect(navBox).not.toBeNull()
      expect((navBox?.y ?? 0) - ((reportBox?.y ?? 0) + (reportBox?.height ?? 0))).toBeGreaterThanOrEqual(47)
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
    })

    await test.step(`${width}px Chat`, async () => {
      await page.goto(`/chat?prescription_id=${ids.prescription}`)
      await expect(page.getByText('무엇을 도와드릴까요?')).toBeVisible()

      const topbar = page.locator('.app-topbar')
      const title = page.getByRole('heading', { name: '도지와 대화하기' })
      const conversation = page.locator('.chat-page__conversation')
      const scroller = page.locator('.chat-messages')
      const composer = page.locator('.chat-composer')
      const nav = page.getByRole('navigation', { name: '주요 메뉴' })

      const topbarBox = await topbar.boundingBox()
      const titleBox = await title.boundingBox()
      const conversationBox = await conversation.boundingBox()
      const composerBox = await composer.boundingBox()
      const navBox = await nav.boundingBox()
      expect(topbarBox).not.toBeNull()
      expect(titleBox).not.toBeNull()
      expect(conversationBox).not.toBeNull()
      expect(composerBox).not.toBeNull()
      expect(navBox).not.toBeNull()
      expect((titleBox?.y ?? 0) - ((topbarBox?.y ?? 0) + (topbarBox?.height ?? 0))).toBeLessThanOrEqual(18)
      expect(conversationBox?.width ?? 0).toBeGreaterThanOrEqual(width < 390 ? 300 : 366)
      expect((navBox?.y ?? 0) - ((composerBox?.y ?? 0) + (composerBox?.height ?? 0))).toBeGreaterThanOrEqual(7)

      const scrollStyles = await scroller.evaluate((element) => ({
        overflowY: getComputedStyle(element).overflowY,
        scrollbarWidth: getComputedStyle(element).scrollbarWidth,
        webkitDisplay: getComputedStyle(element, '::-webkit-scrollbar').display,
      }))
      expect(scrollStyles).toEqual({
        overflowY: 'auto',
        scrollbarWidth: 'none',
        webkitDisplay: 'none',
      })
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
    })
  }
})

test('390px Chat/Guide 일정 CTA는 click/Enter/Space로 기존 Schedule route만 연다', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installRequirementsApi(page, {
    existingPrescription: true,
    existingChat: true,
  })
  const mutations: string[] = []
  page.on('request', (request) => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      mutations.push(`${request.method()} ${new URL(request.url()).pathname}`)
    }
  })

  const openChat = async () => {
    await page.goto(`/chat?prescription_id=${ids.prescription}`)
    await expect(page.getByText('무엇을 도와드릴까요?')).toBeVisible()
    return page.getByRole('button', { name: '복약 일정 설정하기' })
  }

  let cta = await openChat()
  await cta.click()
  await expect(page).toHaveURL((url) => url.pathname === '/schedule')

  cta = await openChat()
  await cta.focus()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL((url) => url.pathname === '/schedule')

  cta = await openChat()
  await cta.focus()
  await page.keyboard.press('Space')
  await expect(page).toHaveURL((url) => url.pathname === '/schedule')

  const openGuide = async () => {
    await page.goto(`/guides/${ids.guide}`)
    return page.getByRole('button', { name: '복용 일정 확인하기' })
  }

  cta = await openGuide()
  await cta.click()
  await expect(page).toHaveURL((url) => url.pathname === '/schedule')

  cta = await openGuide()
  await cta.focus()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL((url) => url.pathname === '/schedule')

  cta = await openGuide()
  await cta.focus()
  await page.keyboard.press('Space')
  await expect(page).toHaveURL((url) => url.pathname === '/schedule')

  expect(mutations).toEqual([])
})

test('390px active Chat은 scrollbar 없이 wheel/keyboard/touch 세로 scrolling을 유지한다', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installRequirementsApi(page, {
    existingPrescription: true,
    existingChat: true,
  })
  await page.goto(`/chat?prescription_id=${ids.prescription}`)
  await expect(page.getByText('무엇을 도와드릴까요?')).toBeVisible()

  const scroller = page.locator('.chat-messages')
  await scroller.evaluate((element) => {
    const spacer = document.createElement('div')
    spacer.setAttribute('aria-hidden', 'true')
    spacer.style.height = '1200px'
    element.append(spacer)
  })
  const box = await scroller.boundingBox()
  expect(box).not.toBeNull()

  await page.mouse.move((box?.x ?? 0) + 20, (box?.y ?? 0) + 20)
  await page.mouse.wheel(0, 280)
  await expect.poll(() => scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)

  await scroller.evaluate((element) => {
    element.scrollTop = 0
    const focusable = element as HTMLElement
    focusable.tabIndex = -1
    focusable.focus()
  })
  await page.keyboard.press('PageDown')
  await expect.poll(() => scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)

  await scroller.evaluate((element) => { element.scrollTop = 0 })
  const client = await page.context().newCDPSession(page)
  const x = (box?.x ?? 0) + (box?.width ?? 0) / 2
  const startY = (box?.y ?? 0) + Math.min((box?.height ?? 0) - 20, 260)
  await client.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x, y: startY }],
  })
  await client.send('Input.dispatchTouchEvent', {
    type: 'touchMove',
    touchPoints: [{ x, y: startY - 160 }],
  })
  await client.send('Input.dispatchTouchEvent', {
    type: 'touchEnd',
    touchPoints: [],
  })
  await expect.poll(() => scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
})
