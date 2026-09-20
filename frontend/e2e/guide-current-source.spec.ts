import { expect, test } from '@playwright/test'

const guideId = '11111111-1111-4111-8111-111111111111'
const prescriptionId = '22222222-2222-4222-8222-222222222222'

const guideContent = [
  '복약 가이드',
  [
    '[1] 합성 처방약',
    '용량: 1 정',
    '복용 횟수: 하루 2회',
    '복용 시점: 아침 저녁 식후',
    '복용 기간: 7일',
    '복용 시 주의해야 할 점: 처방에 안내된 복용 계획을 확인해 주세요.',
    '주의해야 할 음식·음료: 의료진 또는 약사에게 확인해 주세요.',
    '나타날 수 있는 불편감: 불편감이 지속되면 의료진과 상담하세요.',
  ].join('\n'),
  '공통 안내: 불명확한 내용은 의료진 또는 약사에게 확인해 주세요.\n안전 안내: 임의로 복용을 중단하거나 변경하지 마세요.',
].join('\n\n')

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', 'synthetic-guide-token')
    document.documentElement.style.setProperty('--ds-safe-bottom', '34px')
  })

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
        id: '33333333-3333-4333-8333-333333333333',
        name: '합성 사용자',
        email: 'synthetic@example.com',
        phone_number: null,
        birthday: null,
        gender: null,
        created_at: '2026-09-16T00:00:00Z',
      })
    }

    if (request.method() === 'GET' && path === `/api/v1/guides/${guideId}`) {
      return json({
        data: {
          guide_id: guideId,
          prescription_id: prescriptionId,
          prescription_version_id: '66666666-6666-4666-8666-666666666666',
          generation_status: 'COMPLETED',
          content: guideContent,
          model_name: 'synthetic-guide-model',
          prompt_version: 'synthetic-guide-v1',
          release_decision: 'PASS',
          release_is_current: true,
          fallback_code: null,
          fallback_text: null,
          citations: [
            {
              source_type: 'LIFESTYLE_GUIDELINE',
              source_code: 'SYNTHETIC-GUIDELINE',
              source_version: '2026.1',
              locator: 'section-2',
              display_order: 1,
            },
          ],
          requested_at: '2026-09-16T00:00:00Z',
          completed_at: '2026-09-16T00:00:03Z',
        },
      })
    }

    return route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'synthetic not found' }),
    })
  })
})

test('GUIDE-02A/02B가 320/390/412px에서 overflow와 Bottom Navigation 겹침 없이 유지된다', async ({ page }) => {
  for (const width of [320, 390, 412]) {
    await test.step(`${width}px`, async () => {
      await page.setViewportSize({ width, height: 844 })
      await page.goto(`/guides/${guideId}`)

      const toggle = page.getByRole('button', { name: /합성 처방약/ })
      await expect(toggle).toHaveAttribute('aria-expanded', 'false')
      await toggle.click()
      await expect(toggle).toHaveAttribute('aria-expanded', 'true')
      await expect(page.getByRole('heading', { name: '임신·수유 중 안내' })).toBeVisible()
      await expect(page.getByText('현재 제공된 안내가 없어요.')).toHaveCount(3)
      await expect(page.getByRole('heading', { name: '안내 근거' })).toBeVisible()
      await expect(page.getByText('SYNTHETIC-GUIDELINE')).toBeVisible()

      const content = page.locator('.guide-page__content')
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
      expect(await content.evaluate((element) => element.scrollWidth - element.clientWidth)).toBe(0)

      await content.evaluate((element) => element.scrollTo(0, element.scrollHeight))
      const chatButton = page.getByRole('button', { name: '복약 챗봇 도지와 이야기하기' })
      const navigation = page.getByRole('navigation', { name: '주요 메뉴' })
      const chatBox = await chatButton.boundingBox()
      const navigationBox = await navigation.boundingBox()
      expect(chatBox).not.toBeNull()
      expect(navigationBox).not.toBeNull()
      expect((navigationBox?.y ?? 0) - ((chatBox?.y ?? 0) + (chatBox?.height ?? 0))).toBeGreaterThanOrEqual(12)
    })
  }
})

test('runtime STALE은 빈 Guide가 아닌 승인된 fallback으로 표시한다', async ({ page }) => {
  await page.route(`**/api/v1/guides/${guideId}`, async (route) => {
    await route.fulfill({
      json: {
        data: {
          guide_id: guideId,
          prescription_id: prescriptionId,
          prescription_version_id: '66666666-6666-4666-8666-666666666666',
          generation_status: 'COMPLETED',
          content: null,
          model_name: 'synthetic-guide-model',
          prompt_version: 'synthetic-guide-v1',
          release_decision: 'STALE',
          release_is_current: false,
          fallback_code: 'PRESCRIPTION_STALE',
          fallback_text: '처방 정보가 변경되어 최신 안내를 다시 확인해야 해요.',
          citations: [],
          requested_at: '2026-09-16T00:00:00Z',
          completed_at: '2026-09-16T00:00:03Z',
        },
      },
    })
  })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/guides/${guideId}`)

  await expect(page.getByRole('heading', { name: '처방 정보가 변경되었어요' })).toBeVisible()
  await expect(page.getByText('처방 정보가 변경되어 최신 안내를 다시 확인해야 해요.')).toBeVisible()
  await expect(page.getByText('가이드 내용이 아직 없어요')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
})

test('약 카드는 keyboard Enter/Space로 펼치고 접으며 의료 섹션 순서를 유지한다', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/guides/${guideId}`)

  const toggle = page.getByRole('button', { name: /합성 처방약/ })
  await toggle.focus()
  await page.keyboard.press('Enter')
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')

  const sectionHeadings = await page.locator('.guide-page__medical-sections h4').allTextContents()
  expect(sectionHeadings).toEqual([
    '복용 시 주의해야 할 점',
    '주의해야 할 음식·음료',
    '음주/흡연 안내',
    '나타날 수 있는 불편감',
    '이런 증상은 병원에 가세요',
    '임신·수유 중 안내',
  ])

  await toggle.focus()
  await page.keyboard.press('Space')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
})

test('실패 가이드 재시도는 POST 후 새 가이드 상세를 표시한다', async ({ page }) => {
  const newGuideId = '44444444-4444-4444-8444-444444444444'
  let creationCount = 0
  await page.route('**/api/v1/guides**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const creating = request.method() === 'POST'
    if (creating) {
      expect(path).toBe('/api/v1/guides')
      expect(request.postDataJSON()).toEqual({ prescription_id: prescriptionId })
      creationCount += 1
    }
    const completed = creating || path === `/api/v1/guides/${newGuideId}`
    await route.fulfill({
      json: { data: {
        guide_id: completed ? newGuideId : guideId,
        prescription_id: prescriptionId,
        prescription_version_id: '55555555-5555-4555-8555-555555555555',
        generation_status: completed ? 'COMPLETED' : 'FAILED',
        content: completed ? guideContent : null,
        model_name: 'synthetic-guide-model',
        prompt_version: 'synthetic-guide-v1',
        release_decision: null,
        release_is_current: null,
        fallback_code: null,
        fallback_text: null,
        citations: [],
        requested_at: '2026-09-16T00:00:00Z',
        completed_at: '2026-09-16T00:00:03Z',
      } },
    })
  })
  await page.goto(`/guides/${guideId}`)
  await expect(page.getByRole('heading', { name: '가이드를 만들지 못했어요' })).toBeVisible()
  await page.getByRole('button', { name: '다시 시도하기' }).click()
  await expect(page).toHaveURL(`/guides/${newGuideId}`)
  await expect(page.getByRole('button', { name: /합성 처방약/ })).toBeVisible()
  expect(creationCount).toBe(1)
})
