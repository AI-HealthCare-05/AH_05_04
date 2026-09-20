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
    if (!['/login', '/signup'].includes(window.location.pathname)) {
      localStorage.setItem('access_token', token)
    }
    document.documentElement.style.setProperty('--ds-safe-bottom', '34px')
  }, syntheticToken)
})

test('320/390/412px에서 전체 대상 화면은 가로 overflow와 Bottom Navigation 겹침이 없다', async ({ page }) => {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'GET' && path === '/api/v1/users/me') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: ids.user,
          name: '합성 사용자',
          email: 'synthetic@example.com',
          phone_number: null,
          birthday: null,
          gender: null,
          created_at: '2026-09-15T00:00:00Z',
        }),
      })
      return
    }
    if (request.method() === 'GET') {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'synthetic visual state' }),
      })
      return
    }
    await route.abort()
  })

  const targets = [
    { name: 'Signup', path: '/signup', selector: '.mvp-signup-page .app-scroll', hasNavigation: false },
    { name: 'Login', path: '/login', selector: '.mvp-login-page .app-scroll', hasNavigation: false },
    { name: 'Schedule', path: '/schedule', selector: '.schedule-page .app-scroll', hasNavigation: true },
    { name: 'Report', path: '/report', selector: '.mvp-report-page .app-scroll', hasNavigation: true },
    { name: 'Menu', path: '/menu', selector: '.mvp-menu-page .app-scroll', hasNavigation: true },
    { name: 'Notification', path: '/notifications', selector: '.mvp-notifications-page .app-scroll', hasNavigation: false },
  ] as const

  for (const width of mobileWidths) {
    await page.setViewportSize({ width, height: 844 })
    for (const target of targets) {
      await test.step(`${width}px ${target.name}`, async () => {
        await page.goto(target.path)
        const content = page.locator(target.selector)
        await expect(content).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
        expect(await content.evaluate((element) => element.scrollWidth - element.clientWidth)).toBe(0)

        if (target.hasNavigation) {
          const nav = page.getByRole('navigation', { name: '주요 메뉴' })
          await content.evaluate((element) => element.scrollTo(0, element.scrollHeight))
          const lastContent = content.locator(':scope > *').last()
          const lastBox = await lastContent.boundingBox()
          const navBox = await nav.boundingBox()
          expect(lastBox).not.toBeNull()
          expect(navBox).not.toBeNull()
          expect((navBox?.y ?? 0) - ((lastBox?.y ?? 0) + (lastBox?.height ?? 0))).toBeGreaterThanOrEqual(12)
        }
      })
    }
  }
})

test('Schedule CURRENT Source 상태와 320/390/412px 레이아웃을 유지한다', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-09-16T05:00:00Z')) // KST 14:00

  const occurrenceIds = [
    '77777777-7777-4777-8777-777777777771',
    '77777777-7777-4777-8777-777777777772',
    '77777777-7777-4777-8777-777777777773',
  ]
  let scheduleStatus: 'READY' | 'PARTIAL' | 'SETUP_REQUIRED' | 'INACTIVE' | 'NO_ACTIVE_PRESCRIPTION' = 'READY'
  const scheduleMutations: string[] = []
  const stateCopy = {
    PARTIAL: '일부 약의 시간이 비어 있어요',
    SETUP_REQUIRED: '일정 설정이 필요해요',
    INACTIVE: '현재 사용 중인 일정이 없어요',
    NO_ACTIVE_PRESCRIPTION: '활성 처방이 필요해요',
  } as const

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const json = (body: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })

    if (request.method() === 'GET' && path === '/api/v1/users/me') {
      return json({
        id: ids.user,
        name: '합성 사용자',
        email: 'synthetic@example.com',
        phone_number: null,
        birthday: null,
        gender: null,
        created_at: '2026-09-16T00:00:00Z',
      })
    }
    if (request.method() === 'GET' && path === '/api/v1/medication-occurrences') {
      const scheduleItemStatus = scheduleStatus === 'INACTIVE'
        ? 'INACTIVE'
        : scheduleStatus === 'READY'
          ? 'READY'
          : 'SETUP_REQUIRED'
      return json({
        data: {
          schedule_status: scheduleStatus,
          schedule_items: scheduleStatus === 'NO_ACTIVE_PRESCRIPTION' ? [] : [{
            prescription_version_medication_id: ids.prescriptionVersionMedication,
            schedule_item_status: scheduleItemStatus,
            schedule_id: scheduleStatus === 'SETUP_REQUIRED' ? null : '88888888-8888-4888-8888-888888888888',
            revision: scheduleStatus === 'SETUP_REQUIRED' ? null : 1,
            setup_reason: scheduleStatus === 'READY' ? null : 'USER_CONFIRMATION_REQUIRED',
          }],
          occurrences: ['READY', 'PARTIAL', 'INACTIVE'].includes(scheduleStatus)
            ? occurrenceIds.map((occurrenceId, index) => ({
                occurrence_id: occurrenceId,
                prescription_version_id: ids.prescriptionVersion,
                prescription_version_medication_id: ids.prescriptionVersionMedication,
                scheduled_local_date: '2026-09-16',
                scheduled_at: [
                  '2026-09-15T23:00:00Z',
                  '2026-09-16T04:00:00Z',
                  '2026-09-16T11:00:00Z',
                ][index],
                confirmation_deadline_at: '2026-09-16T18:00:00Z',
                status: 'PENDING',
                checkin: null,
              }))
            : [],
        },
      })
    }
    if (request.method() === 'GET' && path === '/api/v1/prescriptions/latest') {
      return json({
        data: {
          prescription_id: ids.prescription,
          prescription_version_id: ids.prescriptionVersion,
          revision: 1,
          current: true,
          document_id: ids.document,
          prescribed_date: '2026-09-16',
          confirmed_at: '2026-09-16T00:00:00Z',
          medications: [{
            prescription_version_medication_id: ids.prescriptionVersionMedication,
            medication_name: '합성 혈압약',
            strength_text: '5mg',
            dose_value: 1,
            dose_unit: '정',
            frequency_per_day: 3,
            timing_text: null,
            duration_days: 7,
            display_order: 0,
          }],
        },
      })
    }
    if (request.method() === 'GET' && path.endsWith('/medication')) {
      const occurrenceId = path.split('/').at(-2)
      return json({
        data: {
          occurrence_id: occurrenceId,
          prescription_version_id: ids.prescriptionVersion,
          prescription_version_medication_id: ids.prescriptionVersionMedication,
          medication_name: '합성 혈압약',
          strength_text: '5mg',
          dose_value: 1,
          dose_unit: '정',
        },
      })
    }
    if (
      request.method() === 'PUT' &&
      path === `/api/v1/prescription-version-medications/${ids.prescriptionVersionMedication}/schedule`
    ) {
      scheduleMutations.push(await request.postData() ?? '')
      scheduleStatus = 'READY'
      return json({ data: {} })
    }
    await route.abort()
  })

  for (const width of mobileWidths) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/schedule?date=2026-09-16')
    await expect(page.getByRole('heading', { name: '오늘의 복약' })).toBeVisible()
    await expect(page.getByRole('button', { name: '복용 여부 기록하기' })).toHaveCount(2)
    await expect(page.getByText('복용 예정')).toHaveCount(1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
    const content = page.locator('.schedule-page .app-scroll')
    expect(await content.evaluate((element) => element.scrollWidth - element.clientWidth)).toBe(0)
    await content.evaluate((element) => element.scrollTo(0, element.scrollHeight))
    const settings = page.getByRole('button', { name: '복약 일정 설정·수정' })
    const nav = page.getByRole('navigation', { name: '주요 메뉴' })
    const settingsBox = await settings.boundingBox()
    const navBox = await nav.boundingBox()
    expect(settingsBox).not.toBeNull()
    expect(navBox).not.toBeNull()
    expect((navBox?.y ?? 0) - ((settingsBox?.y ?? 0) + (settingsBox?.height ?? 0))).toBeGreaterThanOrEqual(12)
  }

  await page.setViewportSize({ width: 390, height: 844 })
  for (const status of ['SETUP_REQUIRED', 'PARTIAL', 'INACTIVE', 'NO_ACTIVE_PRESCRIPTION'] as const) {
    scheduleStatus = status
    await page.goto('/schedule?date=2026-09-16')
    await expect(page.getByRole('heading', { name: stateCopy[status] })).toBeVisible()
    if (status === 'PARTIAL' || status === 'INACTIVE') {
      await expect(page.getByRole('heading', { name: '오늘의 복약' })).toBeVisible()
      await expect(page.getByRole('button', { name: '복용 여부 기록하기' })).toHaveCount(2)
      await expect(page.getByText('복용 예정')).toHaveCount(1)
    } else {
      await expect(page.getByRole('heading', { name: '오늘의 복약' })).toHaveCount(0)
    }
  }

  scheduleStatus = 'SETUP_REQUIRED'
  for (const width of mobileWidths) {
    await test.step(`${width}px 통합 Schedule 편집 화면`, async () => {
      await page.setViewportSize({ width, height: 844 })
      await page.goto('/schedule?date=2026-09-16')
      await page.getByRole('button', { name: '일정 설정하기' }).click()
      await expect(page.getByRole('heading', { name: '복용할 날짜와 시간을 확인해 주세요' })).toBeVisible()
      await expect(page.getByRole('heading', { name: '합성 혈압약' })).toBeVisible()
      await expect(page.getByLabel(/합성 혈압약 .*번째 복용 시간/)).toHaveCount(3)
      await expect(page.getByRole('button', { name: '복약 일정 저장하기' })).toHaveCount(1)

      const content = page.locator('.schedule-page .app-scroll')
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
      expect(await content.evaluate((element) => element.scrollWidth - element.clientWidth)).toBe(0)
      expect(await page.getByLabel('합성 혈압약 복용 시작일').evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(52)

      await content.evaluate((element) => element.scrollTo(0, element.scrollHeight))
      const cta = page.getByRole('button', { name: '복약 일정 저장하기' })
      const nav = page.getByRole('navigation', { name: '주요 메뉴' })
      const ctaBox = await cta.boundingBox()
      const navBox = await nav.boundingBox()
      expect(ctaBox).not.toBeNull()
      expect(navBox).not.toBeNull()
      expect((navBox?.y ?? 0) - ((ctaBox?.y ?? 0) + (ctaBox?.height ?? 0))).toBeGreaterThanOrEqual(12)
    })
  }

  for (const [index, width] of mobileWidths.entries()) {
    await test.step(`${width}px 저장 후 최신 일정 즉시 표시`, async () => {
      scheduleStatus = 'SETUP_REQUIRED'
      await page.setViewportSize({ width, height: 844 })
      await page.goto('/schedule?date=2026-09-16')
      await page.getByRole('button', { name: '일정 설정하기' }).click()
      await page.getByLabel('합성 혈압약 복용 시작일').fill('2026-09-16')
      await page.getByLabel('합성 혈압약 복용 종료일').fill('2026-09-22')
      await page.getByLabel('합성 혈압약 1번째 복용 시간').fill('08:00')
      await page.getByLabel('합성 혈압약 2번째 복용 시간').fill('13:00')
      await page.getByLabel('합성 혈압약 3번째 복용 시간').fill('20:00')
      const cta = page.getByRole('button', { name: '복약 일정 저장하기' })
      await cta.focus()
      await expect(cta).toBeFocused()
      await page.keyboard.press('Enter')
      await expect.poll(() => scheduleMutations.length).toBe(index + 1)
      await expect(page.getByRole('heading', { name: '오늘의 복약' })).toBeVisible()
      await expect(page.getByRole('button', { name: '복용 여부 기록하기' })).toHaveCount(2)
      await expect(page.getByText('복용 예정')).toHaveCount(1)
      await expect(page.getByRole('heading', { name: '복용할 날짜와 시간을 확인해 주세요' })).toHaveCount(0)
    })
  }
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
