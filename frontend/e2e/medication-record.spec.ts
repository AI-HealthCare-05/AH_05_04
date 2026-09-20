import { expect, test } from '@playwright/test'
import { ids, syntheticToken } from './fixtures/requirementsApi'

const mobileWidths = [320, 390, 412] as const

// 고정 시계: KST 2026-09-16(수) 14:00.
// occurrence는 KST 08:00 / 13:00 / 20:00 이므로 앞 2건만 기록 가능하다.
const FIXED_NOW = new Date('2026-09-16T05:00:00Z')
const TODAY = '2026-09-16'
const MONDAY = '2026-09-14'

const occurrenceIds = [
  '88888888-8888-4888-8888-888888888881',
  '88888888-8888-4888-8888-888888888882',
  '88888888-8888-4888-8888-888888888883',
]

const scheduledAtByDate: Record<string, string[]> = {
  [TODAY]: [
    '2026-09-15T23:00:00Z',
    '2026-09-16T04:00:00Z',
    '2026-09-16T11:00:00Z',
  ],
  // 과거 날짜는 1건만 두어 재조회를 구분할 수 있게 한다.
  [MONDAY]: ['2026-09-13T23:00:00Z'],
}

type RecordApiState = {
  requestedDates: string[]
  // StrictMode에서 load effect가 중복 실행되므로 호출 횟수가 아니라
  // 상태 플래그로 실패를 제어한다.
  failDayRequests: boolean
}

async function installRecordApi(page: import('@playwright/test').Page) {
  const state: RecordApiState = { requestedDates: [], failDayRequests: false }

  const json = (
    route: import('@playwright/test').Route,
    body: unknown,
    status = 200,
  ) => route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (request.method() === 'GET' && path === '/api/v1/users/me') {
      return json(route, {
        id: ids.user,
        name: '합성 사용자',
        email: 'synthetic@example.com',
        phone_number: null,
        birthday: null,
        gender: null,
        created_at: '2026-09-15T00:00:00Z',
      })
    }

    if (request.method() === 'GET' && path === '/api/v1/medication-occurrences') {
      const date = url.searchParams.get('date') ?? ''
      state.requestedDates.push(date)

      if (state.failDayRequests) {
        return json(route, { detail: 'synthetic failure' }, 500)
      }

      const scheduledAt = scheduledAtByDate[date] ?? []
      return json(route, {
        data: {
          schedule_status: 'READY',
          schedule_items: [],
          occurrences: scheduledAt.map((value, index) => ({
            occurrence_id: occurrenceIds[index],
            prescription_version_id: ids.prescriptionVersion,
            prescription_version_medication_id: ids.prescriptionVersionMedication,
            scheduled_local_date: date,
            scheduled_at: value,
            confirmation_deadline_at: `${date}T18:00:00Z`,
            status: 'PENDING',
            checkin: null,
          })),
        },
      })
    }

    if (request.method() === 'GET' && path.endsWith('/medication')) {
      const occurrenceId = path.split('/').at(-2)
      return json(route, {
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

    if (request.method() === 'GET') {
      return json(route, { detail: 'synthetic visual state' }, 404)
    }

    await route.abort()
  })

  return state
}

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(FIXED_NOW)
  await page.addInitScript((token) => {
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', token)
    document.documentElement.style.setProperty('--ds-safe-bottom', '34px')
  }, syntheticToken)
})

test('RECORD-01 · Menu 진입과 320/390/412px 날짜 selector 레이아웃', async ({ page }) => {
  await installRecordApi(page)

  for (const width of mobileWidths) {
    await test.step(`${width}px`, async () => {
      await page.setViewportSize({ width, height: 844 })

      // 1) Menu에서 "복약 기록" 진입
      await page.goto('/menu')
      await page.getByRole('button', { name: '복약 기록', exact: true }).click()
      await expect(page).toHaveURL(/\/records$/)

      // 2) h1 노출
      await expect(
        page.getByRole('heading', { name: '복약 기록', level: 1 }),
      ).toBeVisible()

      // 3) 가로 overflow 없음 (문서 + 스크롤 영역 + 날짜 selector)
      const content = page.locator('.medication-record-page .app-scroll')
      await expect(content).toBeVisible()
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
      ).toBe(0)
      expect(
        await content.evaluate((element) => element.scrollWidth - element.clientWidth),
      ).toBe(0)

      const days = page.getByRole('group', { name: '날짜 선택' })
      expect(
        await days.evaluate((element) => element.scrollWidth - element.clientWidth),
      ).toBe(0)

      // 9) Bottom Navigation "일정" active (복약 일정과 같은 복약 관리 영역)
      const nav = page.getByRole('navigation', { name: '주요 메뉴' })
      await expect(
        nav.locator('button[aria-current="page"]'),
      ).toHaveText(/일정/)
    })
  }
})

test('RECORD-01 · 7일 selector의 과거/오늘/미래 상태와 날짜별 재조회', async ({ page }) => {
  const state = await installRecordApi(page)

  await page.goto('/records')
  await expect(
    page.getByRole('heading', { name: '복약 기록', level: 1 }),
  ).toBeVisible()

  const dayButtons = page.getByRole('group', { name: '날짜 선택' }).getByRole('button')

  // 4) 월~일 고정 7일, 2026-09-16(수) 기준 목~일 disabled
  await expect(dayButtons).toHaveCount(7)
  await expect(dayButtons.nth(0)).toBeEnabled()
  await expect(dayButtons.nth(2)).toBeEnabled()
  await expect(dayButtons.nth(2)).toHaveAttribute('aria-current', 'date')
  await expect(dayButtons.nth(3)).toBeDisabled()
  await expect(dayButtons.nth(6)).toBeDisabled()

  // 오늘 날짜로 먼저 조회됐는지 확인
  await expect.poll(() => state.requestedDates).toContain(TODAY)

  // 5) 과거 날짜 선택 → 해당 date로 재조회
  await dayButtons.nth(0).click()
  await expect(page).toHaveURL(new RegExp(`date=${MONDAY}$`))
  await expect.poll(() => state.requestedDates).toContain(MONDAY)

  // 과거 날짜 fixture는 1건뿐이다.
  await expect(page.locator('.medication-record-card')).toHaveCount(1)
})

test('RECORD-01 · 예정 시각 전 PENDING은 read-only, 기록 가능 건은 CHECKIN-01로 이동', async ({ page }) => {
  await installRecordApi(page)

  await page.goto('/records')
  await expect(page.locator('.medication-record-card')).toHaveCount(3)

  // 6) 20:00 건은 read-only: chevron/CTA 없음 + 안내 노출
  const readOnlyCard = page.locator('.medication-record-card--readonly')
  await expect(readOnlyCard).toHaveCount(1)
  await expect(readOnlyCard.locator('.medication-record-card__chevron')).toHaveCount(0)
  await expect(
    readOnlyCard.getByRole('button', { name: '복용 여부 기록하기' }),
  ).toHaveCount(0)
  await expect(
    page.getByText('20:00부터 복약 기록을 남길 수 있어요.'),
  ).toBeVisible()

  // 기록 가능한 08:00 / 13:00 건에는 chevron과 CTA가 있다.
  await expect(
    page.getByRole('button', { name: '복용 여부 기록하기' }),
  ).toHaveCount(2)
  await expect(page.locator('.medication-record-card__chevron')).toHaveCount(2)

  // 7) 기존 Check-in 화면(CHECKIN-01)으로 이동한다. RECORD-02는 아직 없다.
  await page.getByRole('button', { name: '복용 여부 기록하기' }).first().click()
  await expect(page).toHaveURL(
    new RegExp(`/schedule/occurrences/${occurrenceIds[0]}`),
  )
})

test('RECORD-01 · Back은 MENU-01로 돌아간다', async ({ page }) => {
  await installRecordApi(page)

  await page.goto('/records')
  await expect(
    page.getByRole('heading', { name: '복약 기록', level: 1 }),
  ).toBeVisible()

  // 8) Back → /menu
  await page.getByRole('button', { name: '이전 화면' }).click()
  await expect(page).toHaveURL(/\/menu$/)
})

test('RECORD-01 · 조회 실패 후 다시 시도하면 기록을 불러온다', async ({ page }) => {
  const state = await installRecordApi(page)
  state.failDayRequests = true

  await page.goto('/records')

  // 10) error → retry 상태. StrictMode 중복 호출과 무관하게 실패가 유지된다.
  await expect(
    page.getByRole('heading', { name: '복약 기록을 불러오지 못했어요' }),
  ).toBeVisible()
  await expect(page.locator('.medication-record-card')).toHaveCount(0)

  state.failDayRequests = false
  await page.getByRole('button', { name: '다시 시도' }).click()

  await expect(page.locator('.medication-record-card')).toHaveCount(3)
  await expect(
    page.getByRole('heading', { name: '복약 기록을 불러오지 못했어요' }),
  ).toHaveCount(0)
})
