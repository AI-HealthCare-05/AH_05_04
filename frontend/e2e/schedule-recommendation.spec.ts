import { expect, test } from '@playwright/test'
import { ids, syntheticToken } from './fixtures/requirementsApi'

for (const width of [320, 390]) {
  test(`${width}px schedule setup uses explicit manual time without recommendation UI`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await page.addInitScript(
      (token) => localStorage.setItem('access_token', token),
      syntheticToken,
    )

    const writes: unknown[] = []
    let recommendationRequests = 0
    let scheduleStatus: 'SETUP_REQUIRED' | 'READY' = 'SETUP_REQUIRED'

    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      const json = (data: unknown) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(data),
        })

      if (path === '/api/v1/users/me') {
        return json({ id: ids.user, name: '합성 사용자' })
      }

      if (
        request.method() === 'GET' &&
        path === '/api/v1/medication-occurrences'
      ) {
        const ready = scheduleStatus === 'READY'

        return json({
          data: {
            schedule_status: scheduleStatus,
            occurrences: [],
            schedule_items: [
              {
                prescription_version_medication_id:
                  ids.prescriptionVersionMedication,
                schedule_item_status: ready ? 'READY' : 'SETUP_REQUIRED',
                schedule_id: ready
                  ? '88888888-8888-4888-8888-888888888888'
                  : null,
                revision: ready ? 1 : null,
                setup_reason: ready ? null : 'MISSING_START_DATE',
                schedule: ready
                  ? {
                      schedule_id: '88888888-8888-4888-8888-888888888888',
                      prescription_version_medication_id:
                        ids.prescriptionVersionMedication,
                      revision: 1,
                      status: 'ACTIVE',
                      start_local_date: '2026-09-17',
                      end_mode: 'DATE',
                      end_local_date: '2026-09-23',
                      local_times: ['20:00'],
                    }
                  : null,
              },
            ],
          },
        })
      }

      if (
        request.method() === 'GET' &&
        path === '/api/v1/prescriptions/latest'
      ) {
        return json({
          data: {
            prescription_id: ids.prescription,
            prescription_version_id: ids.prescriptionVersion,
            revision: 1,
            current: true,
            document_id: ids.document,
            prescribed_date: '2026-09-17',
            confirmed_at: '2026-09-17T00:00:00Z',
            medications: [
              {
                prescription_version_medication_id:
                  ids.prescriptionVersionMedication,
                medication_name: '합성 검증약',
                strength_text: null,
                dose_value: 1,
                dose_unit: '정',
                frequency_per_day: 1,
                timing_text: '저녁 식후 30분',
                duration_days: 7,
                display_order: 0,
              },
            ],
          },
        })
      }

      if (path.endsWith('/schedule-recommendation')) {
        recommendationRequests += 1
        return route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: '{}',
        })
      }

      if (
        request.method() === 'PUT' &&
        path.endsWith('/schedule')
      ) {
        writes.push(request.postDataJSON())
        scheduleStatus = 'READY'
        return json({ data: {} })
      }

      return route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: '{}',
      })
    })

    await page.goto('/schedule?date=2026-09-17')
    await page.getByRole('button', { name: '일정 설정하기' }).click()

    await expect(
      page.getByText(/Dosey는 복용 시간을 추정하거나 추천하지 않아요/),
    ).toBeVisible()

    await expect(
      page.getByLabel('합성 검증약 저녁 식사 종료 시각'),
    ).toHaveCount(0)

    await expect(
      page.getByRole('button', { name: '시간 후보 계산' }),
    ).toHaveCount(0)

    await expect(
      page.getByRole('button', { name: '후보 적용' }),
    ).toHaveCount(0)

    const timeInput = page.getByLabel('합성 검증약 1번째 복용 시간')
    await expect(timeInput).toBeEnabled()
    await timeInput.fill('20:00')

    expect(recommendationRequests).toBe(0)

    await page.getByLabel('합성 검증약 복용 시작일').fill('2026-09-17')
    await page.getByLabel('합성 검증약 복용 종료일').fill('2026-09-23')

    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true)

    await page
      .getByRole('button', { name: '복약 일정 저장하기' })
      .click()

    await expect.poll(() => writes.length).toBe(1)

    expect(writes[0]).toMatchObject({
      local_times: ['20:00'],
      expected_revision: 0,
    })
    expect(writes[0]).not.toHaveProperty('recommendation_context')
    expect(recommendationRequests).toBe(0)
  })
}
