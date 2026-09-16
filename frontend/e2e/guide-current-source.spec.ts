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
          generation_status: 'COMPLETED',
          content: guideContent,
          model_name: 'synthetic-guide-model',
          prompt_version: 'synthetic-guide-v1',
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
