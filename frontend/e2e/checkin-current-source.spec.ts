import { expect, test } from '@playwright/test'
import { ids, syntheticToken } from './fixtures/requirementsApi'

const mobileWidths = [320, 390, 412] as const

// KST 14:00 고정. fixture occurrence는 KST 13:00 2건.
const FIXED_NOW = new Date('2026-09-16T05:00:00Z')
const DATE = '2026-09-16'

const OCC_A = '11111111-1111-4111-8111-111111111111'
const OCC_B = '22222222-2222-4222-8222-222222222222'
const MED_A = '33333333-3333-4333-8333-333333333331'
const MED_B = '33333333-3333-4333-8333-333333333332'

function medicationIdForOccurrence(occurrenceId: string): string {
  return occurrenceId === OCC_A ? MED_A : MED_B
}

type CheckinApiState = {
  putCalls: { occurrenceId: string; status: string }[]
  failPuts: boolean
}

async function installCheckinApi(page: import('@playwright/test').Page) {
  const state: CheckinApiState = { putCalls: [], failPuts: false }

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
      return json(route, {
        data: {
          schedule_status: 'READY',
          schedule_items: [
        {
          prescription_version_medication_id: MED_A,
          schedule_item_status: 'READY',
          schedule_id: 'schedule-a',
          revision: 1,
          setup_reason: null,
          schedule: {
            schedule_id: 'schedule-a',
            prescription_version_medication_id: MED_A,
            revision: 1,
            status: 'ACTIVE',
            start_local_date: DATE,
            end_mode: 'OPEN_ENDED',
            end_local_date: null,
            local_times: ['08:00', '13:00'],
          },
        },
        {
          prescription_version_medication_id: MED_B,
          schedule_item_status: 'READY',
          schedule_id: 'schedule-b',
          revision: 1,
          setup_reason: null,
          schedule: {
            schedule_id: 'schedule-b',
            prescription_version_medication_id: MED_B,
            revision: 1,
            status: 'ACTIVE',
            start_local_date: DATE,
            end_mode: 'OPEN_ENDED',
            end_local_date: null,
            local_times: ['13:00'],
          },
        },
      ],
          occurrences: [OCC_A, OCC_B].map((occurrenceId) => ({
            occurrence_id: occurrenceId,
            prescription_version_id: ids.prescriptionVersion,
            prescription_version_medication_id: medicationIdForOccurrence(occurrenceId),
            scheduled_local_date: DATE,
            scheduled_at: '2026-09-16T04:00:00Z',
            confirmation_deadline_at: '2026-09-16T18:00:00Z',
            status: 'PENDING',
            checkin: null,
          })),
        },
      })
    }

    if (request.method() === 'GET' && path.endsWith('/medication')) {
      const occurrenceId = path.split('/').at(-2) ?? ''
      return json(route, {
        data: {
          occurrence_id: occurrenceId,
          prescription_version_id: ids.prescriptionVersion,
          prescription_version_medication_id: medicationIdForOccurrence(occurrenceId),
          medication_name: occurrenceId === OCC_A ? '메트포르민정' : '암로디핀정',
          strength_text: occurrenceId === OCC_A ? '500mg' : '5mg',
          dose_value: 1,
          dose_unit: '정',
        },
      })
    }

    if (request.method() === 'PUT' && path.endsWith('/check-in')) {
      const occurrenceId = path.split('/').at(-2) ?? ''
      const body = request.postDataJSON() as { status: string }
      if (state.failPuts) {
        return json(route, { detail: 'synthetic failure' }, 500)
      }
      state.putCalls.push({ occurrenceId, status: body.status })
      return json(route, {
        data: {
          checkin_id: `chk-${occurrenceId}`,
          occurrence_id: occurrenceId,
          status: body.status,
          taken_at: null,
          revision: 1,
          corrected: false,
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

test('CHECKIN-01 · 같은 시간 약 2개를 모두 선택해야 기록하기가 활성화된다', async ({ page }) => {
  await installCheckinApi(page)
  await page.goto(`/schedule/occurrences/${OCC_A}?date=${DATE}`)

  await expect(
    page.getByRole('heading', { name: '13:00 약을 확인해 주세요' }),
  ).toBeVisible()
  await expect(page.locator('.checkin-medication-card')).toHaveCount(2)

  const cta = page.getByRole('button', { name: '기록하기' })
  await expect(cta).toBeDisabled()

  // 일부만 선택하면 여전히 disabled
  await page.getByRole('button', { name: '복용했어요' }).first().click()
  await expect(cta).toBeDisabled()

  // 전부 선택하면 enabled
  await page.getByRole('button', { name: '복용하지 않았어요' }).nth(1).click()
  await expect(cta).toBeEnabled()
})

test('CHECKIN-01 · 혼합 선택을 occurrence별로 저장하고 완료를 표시한다', async ({ page }) => {
  const state = await installCheckinApi(page)
  await page.goto(`/schedule/occurrences/${OCC_A}?date=${DATE}`)

  await page.getByRole('button', { name: '복용했어요' }).first().click()
  await page.getByRole('button', { name: '복용하지 않았어요' }).nth(1).click()
  await page.getByRole('button', { name: '기록하기' }).click()

  await expect(page.getByText('2/2 기록 완료')).toBeVisible()
  expect(state.putCalls).toEqual([
    { occurrenceId: OCC_A, status: 'TAKEN' },
    { occurrenceId: OCC_B, status: 'NOT_TAKEN' },
  ])

  await expect(
    page.getByRole('dialog', { name: '13:00 약 기록을 확인했어요!' }),
  ).toBeVisible()

  await page.getByRole('button', { name: '확인' }).click()

  await expect(
    page.getByRole('dialog', { name: '13:00 약 기록을 확인했어요!' }),
  ).toHaveCount(0)

  await page.getByRole('button', { name: /전체 일정 보기/ }).click()
  await expect(page).toHaveURL(new RegExp(`/schedule\\?date=${DATE}`))
})

test('CHECKIN-01 · 저장 실패 시 선택을 유지하고 다시 시도할 수 있다', async ({ page }) => {
  const state = await installCheckinApi(page)
  state.failPuts = true

  await page.goto(`/schedule/occurrences/${OCC_A}?date=${DATE}`)
  await page.getByRole('button', { name: '복용했어요' }).first().click()
  await page.getByRole('button', { name: '복용했어요' }).nth(1).click()
  await page.getByRole('button', { name: '기록하기' }).click()

  await expect(
    page.getByText('기록을 저장하지 못했어요', { exact: true }),
  ).toBeVisible()
  await expect(page.getByText('2/2 기록 완료')).toHaveCount(0)
  // 선택 유지
  await expect(
    page.getByRole('button', { name: '복용했어요' }).first(),
  ).toHaveAttribute('aria-pressed', 'true')

  state.failPuts = false
  await page.getByRole('button', { name: '저장 다시 시도' }).click()
  await expect(page.getByText('2/2 기록 완료')).toBeVisible()
})

test('CHECKIN-01 · PB-01 요약에서 시간 그룹을 열고 320/390/412px를 유지한다', async ({ page }) => {
  await installCheckinApi(page)

  for (const width of mobileWidths) {
    await test.step(`${width}px`, async () => {
      await page.setViewportSize({ width, height: 844 })
      await page.goto(`/schedule/checkin?date=${DATE}`)

      await expect(
        page.getByRole('heading', { name: '오늘의 복약 체크' }),
      ).toBeVisible()
      await expect(page.locator('.checkin-group-card')).toHaveCount(1)
      await expect(page.locator('.checkin-group-card').first()).toContainText('0/2')

      // 가로 overflow 없음
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
      ).toBe(0)
      const content = page.locator('.checkin-page .app-scroll')
      expect(
        await content.evaluate((element) => element.scrollWidth - element.clientWidth),
      ).toBe(0)

      // Bottom Navigation 일정 active
      const nav = page.getByRole('navigation', { name: '주요 메뉴' })
      await expect(nav.locator('button[aria-current="page"]')).toHaveText(/일정/)

      await page.locator('.checkin-group-card').first().click()
      await expect(
        page.getByRole('heading', { name: '13:00 약을 확인해 주세요' }),
      ).toBeVisible()
    })
  }
})


test('CHECKIN-01 · PB-01 Figma visual QA 390x1180', async ({ page }) => {
  await installCheckinApi(page)

  // 기존 CHECKIN 상세 E2E fixture는 그대로 두고,
  // 이 테스트에서만 Figma PB-01 대표 상태를 덮어쓴다.
  await page.route(
    /\/api\/v1\/medication-occurrences(?:\?.*)?$/,
    async (route) => {
      const makeOccurrence = (
        occurrenceId: string,
        medicationId: string,
        scheduledAt: string,
        checkin: null | {
          checkin_id: string
          occurrence_id: string
          status: 'TAKEN'
          taken_at: string | null
          revision: number
          corrected: boolean
        } = null,
      ) => ({
        occurrence_id: occurrenceId,
        prescription_version_id: ids.prescriptionVersion,
        prescription_version_medication_id: medicationId,
        scheduled_local_date: DATE,
        scheduled_at: scheduledAt,
        confirmation_deadline_at: '2026-09-16T18:00:00Z',
        status: 'PENDING',
        checkin,
      })

      const taken = (occurrenceId: string) => ({
        checkin_id: `chk-${occurrenceId}`,
        occurrence_id: occurrenceId,
        status: 'TAKEN' as const,
        taken_at: null,
        revision: 1,
        corrected: false,
      })

      const morningA = '44444444-4444-4444-8444-444444444441'
      const morningB = '44444444-4444-4444-8444-444444444442'
      const night = '55555555-5555-4555-8555-555555555555'

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            schedule_status: 'READY',
            schedule_items: [],
            occurrences: [
              // KST 08:00 · 2/2 완료
              makeOccurrence(
                morningA,
                '66666666-6666-4666-8666-666666666661',
                '2026-09-15T23:00:00Z',
                taken(morningA),
              ),
              makeOccurrence(
                morningB,
                '66666666-6666-4666-8666-666666666662',
                '2026-09-15T23:00:00Z',
                taken(morningB),
              ),

              // KST 13:00 · 1/2 확인 필요
              makeOccurrence(
                OCC_A,
                MED_A,
                '2026-09-16T04:00:00Z',
                taken(OCC_A),
              ),
              makeOccurrence(
                OCC_B,
                MED_B,
                '2026-09-16T04:00:00Z',
              ),

              // KST 22:30 · 0/1 예정
              makeOccurrence(
                night,
                '77777777-7777-4777-8777-777777777777',
                '2026-09-16T13:30:00Z',
              ),
            ],
          },
        }),
      })
    },
  )

  await page.setViewportSize({ width: 390, height: 1180 })
  await page.goto(`/schedule/checkin?date=${DATE}`)

  await expect(
    page.getByRole('heading', { name: '오늘의 복약 체크' }),
  ).toBeVisible()

  const groups = page.locator('.checkin-group-card')
  await expect(groups).toHaveCount(3)

  await expect(groups.nth(0)).toContainText('1회차')
  await expect(groups.nth(0)).toContainText('08:00')
  await expect(groups.nth(0)).toContainText('2/2')
  await expect(groups.nth(0)).toContainText('완료')

  await expect(groups.nth(1)).toContainText('2회차')
  await expect(groups.nth(1)).toContainText('13:00')
  await expect(groups.nth(1)).toContainText('1/2')
  await expect(groups.nth(1)).toContainText('확인 필요')

  await expect(groups.nth(2)).toContainText('3회차')
  await expect(groups.nth(2)).toContainText('22:30')
  await expect(groups.nth(2)).toContainText('0/1')
  await expect(groups.nth(2)).toContainText('예정')

  await expect(
    page.getByRole('button', { name: /전체 일정 보기/ }),
  ).toBeVisible()

  await expect(
    page.getByRole('button', { name: /복약 리포트/ }),
  ).toBeVisible()

  await page.screenshot({
    path: 'test-results/checkin-pb01-390x1180.png',
    fullPage: false,
  })
})

test('CHECKIN-01 · PB-02 Figma visual QA 390x1180', async ({ page }) => {
  await installCheckinApi(page)

  await page.setViewportSize({ width: 390, height: 1180 })
  await page.goto(`/schedule/occurrences/${OCC_A}?date=${DATE}`)

  await expect(
    page.getByRole('heading', { name: '13:00 약을 확인해 주세요' }),
  ).toBeVisible()

  await expect(
    page.getByRole('button', { name: '기록하기' }),
  ).toBeDisabled()

  await page.screenshot({
    path: 'test-results/checkin-pb02-390x1180.png',
    fullPage: false,
  })
})

test('CHECKIN-01 · PB-03 Figma visual QA 390x1180', async ({ page }) => {
  await installCheckinApi(page)

  await page.setViewportSize({ width: 390, height: 1180 })
  await page.goto(`/schedule/occurrences/${OCC_A}?date=${DATE}`)

  await page.getByRole('button', { name: '복용했어요' }).first().click()
  await page
    .getByRole('button', { name: '복용하지 않았어요' })
    .nth(1)
    .click()

  await expect(
    page.getByRole('button', { name: '기록하기' }),
  ).toBeEnabled()

  await page.screenshot({
    path: 'test-results/checkin-pb03-390x1180.png',
    fullPage: false,
  })
})

test('CHECKIN-01 · PB-04 Figma visual QA 390x1180', async ({ page }) => {
  const state = await installCheckinApi(page)

  await page.setViewportSize({ width: 390, height: 1180 })
  await page.goto(`/schedule/occurrences/${OCC_A}?date=${DATE}`)

  await page.getByRole('button', { name: '복용했어요' }).first().click()
  await page.getByRole('button', { name: '복용했어요' }).nth(1).click()

  state.failPuts = true
  await page.getByRole('button', { name: '기록하기' }).click()

  await expect(
    page.getByText('기록을 저장하지 못했어요', { exact: true }),
  ).toBeVisible()

  await page.screenshot({
    path: 'test-results/checkin-pb04-390x1180.png',
    fullPage: false,
  })
})

test('CHECKIN-01 · PB-05 Figma visual QA 390x1180', async ({ page }) => {
  await installCheckinApi(page)

  await page.setViewportSize({ width: 390, height: 1180 })
  await page.goto(`/schedule/occurrences/${OCC_A}?date=${DATE}`)

  await page.getByRole('button', { name: '복용했어요' }).first().click()
  await page
    .getByRole('button', { name: '복용하지 않았어요' })
    .nth(1)
    .click()

  await page.getByRole('button', { name: '기록하기' }).click()

  await expect(page.getByText('2/2 기록 완료')).toBeVisible()

  await expect(
    page.getByRole('dialog', { name: '13:00 약 기록을 확인했어요!' }),
  ).toBeVisible()

  await expect(
    page.getByRole('button', { name: '확인' }),
  ).toBeVisible()

  await expect(
    page.getByRole('button', { name: '확인' }),
  ).toBeFocused()

  await page.screenshot({
    path: 'test-results/checkin-pb05-390x1180.png',
    fullPage: false,
  })

  await page.getByRole('button', { name: '확인' }).click()

  await expect(
    page.getByRole('dialog', { name: '13:00 약 기록을 확인했어요!' }),
  ).toHaveCount(0)

  await expect(page.getByText('2/2 기록 완료')).toBeVisible()


})
