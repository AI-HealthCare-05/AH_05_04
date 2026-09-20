import { expect, test } from '@playwright/test'
import type { MedicationDayResponse } from '../src/api/medicationOccurrences'

// Uses the existing report_round_trip_fixture in a fresh isolated dosey_e2e DB.
// No interception: every request is served by the current FastAPI implementation.
test('[REAL-STACK][Track C #139] create, reload, complete and cancel plans through real APIs', async ({ page, request }) => {
  const api = process.env.TRACK_C_E2E_API_URL
  test.skip(!api || process.env.TRACK_C_E2E_SYNTHETIC !== '1', 'requires an isolated synthetic database')
  const login = await request.post(`${api}/api/v1/auth/login`, { data: { email: 'report-420@example.com', password: 'Synthetic1!' } })
  expect(login.status()).toBe(200)
  const { access_token: token } = await login.json()
  const headers = { Authorization: `Bearer ${token}` }
  const date = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(new Date())
  const dayResponse = await request.get(`${api}/api/v1/medication-occurrences?date=${date}`, { headers })
  expect(dayResponse.status()).toBe(200)
  const day: MedicationDayResponse = await dayResponse.json()
  const occurrences = day.data.occurrences.slice(0, 2)
  expect(occurrences).toHaveLength(2)
  await page.addInitScript(value => localStorage.setItem('access_token', value), token)

  for (const index of [0, 1, 2]) {
    const occurrence = occurrences[index % occurrences.length]
    const freshDay = await request.get(`${api}/api/v1/medication-occurrences?date=${date}`, { headers })
    expect(freshDay.status()).toBe(200)
    const fresh: MedicationDayResponse = await freshDay.json()
    const current = fresh.data.occurrences.find(item => item.occurrence_id === occurrence.occurrence_id)!
    const correction = await request.put(`${api}/api/v1/medication-occurrences/${occurrence.occurrence_id}/check-in`, {
      headers: { ...headers, 'Idempotency-Key': `track-c-139-checkin-${crypto.randomUUID()}` },
      data: { status: 'NOT_TAKEN', expected_revision: current.checkin?.revision ?? 0 },
    })
    expect(correction.status()).toBe(200)
    await page.goto(`/schedule/occurrences/${occurrence.occurrence_id}?date=${date}`)
    await page.getByRole('button', { name: '이유와 도움 찾기' }).click()
    await page.getByRole('button', { name: '증상은 없어요' }).click()
    await page.getByRole('radio', { name: index === 2 ? '깜빡했어요' : '일정이나 외출 때문에 어려웠어요' }).check()
    await page.getByRole('button', { name: '선택한 어려움으로 도움 찾기' }).click()
    if (index !== 2) {
      await page.getByRole('radio', { name: index === 0 ? '생활 일정이 바뀌었어요' : '약을 가지고 나오지 않았어요' }).check()
      await page.getByRole('button', { name: '선택한 상황으로 도움 찾기' }).click()
    } else {
      await page.getByRole('radio', { name: '알림을 보거나 듣지 못했어요' }).check()
      await page.getByRole('button', { name: '선택한 상황으로 도움 찾기' }).click()
    }

    await expect(page.getByRole('button', { name: '이 도움 확인하기' })).toBeVisible()
    await page.getByRole('button', { name: '이 도움 확인하기' }).click()

    await expect(page.getByRole('heading', { name: '도움 내용을 설정해 주세요' })).toBeVisible()
    await page.getByRole('button', { name: '선택 내용 확인하기' }).click()

    await expect(page.getByRole('heading', { name: '선택한 내용을 확인해 주세요' })).toBeVisible()
    await page.getByRole('button', { name: '이대로 사용하기' }).click()

    await expect(page.getByRole('heading', { name: '실천 계획을 확인해 주세요' })).toBeVisible()

    const creation = page.waitForResponse(
      response =>
        response.request().method() === 'POST' &&
        response.url().endsWith('/support-action-plans'),
    )
    await page.getByRole('button', { name: '이 계획을 저장하고 시작하기' }).click()
    const createdResponse = await creation
    expect(createdResponse.status()).toBe(200)
    const created = (await createdResponse.json()).data
    await expect(page.getByRole('heading', { name: '실천 계획 목록' })).toBeVisible()
    const activePlanButton = page
      .locator('.track-c-plan-item')
      .filter({ hasText: '진행 중' })
      .filter({
        hasText: index === 1
          ? '일상·이동 중 복약 계획 확인'
          : '복약 일정과 알림 확인',
      })
      .first()

    await activePlanButton.click()
    await expect(page.getByText('진행 중', { exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByText('진행 중', { exact: true })).toBeVisible()
    if (index !== 1) {
      const popupPromise = page.waitForEvent('popup')
      await page.getByRole('link', { name: '일정 확인·설정 (새 탭)' }).click()
      const schedule = await popupPromise
      await expect(schedule.getByRole('heading', { name: '실천 계획의 복약 일정' })).toBeVisible()
      await expect(schedule.getByRole('button', { name: '이 약의 일정 확인·설정' })).toBeVisible()
      await schedule.close()
      if (index === 2) {
        await expect(page.getByRole('button', { name: '완료 확인하기' })).toBeDisabled()
        await page.getByRole('button', { name: '알림 설정 상태 확인' }).click()
        await expect(page.getByText(/알림 설정 없이 복약 일정만 확인한 뒤/)).toBeVisible()
      }
      await page.getByRole('button', { name: '완료 확인하기' }).click()
      await page.getByRole('checkbox', { name: index === 2 ? '알림 설정 없이 복약 일정만 확인했어요.' : '기존 복약 일정을 확인했거나 일정 저장을 마쳤어요.' }).check()
      await page.getByRole('button', { name: '완료로 저장' }).click()
      await expect(page.getByText('완료됨', { exact: true })).toBeVisible()
    } else {
      await page.getByRole('button', { name: '계획 취소하기' }).click()
      await page.getByRole('checkbox', { name: '이 계획을 취소할게요.' }).check()
      await page.getByRole('button', { name: '취소로 저장' }).click()
      await expect(page.getByText('취소됨', { exact: true })).toBeVisible()
    }
    const savedResponse = await request.get(`${api}/api/v1/support-action-plans/${created.support_action_plan_id}`, { headers })
    expect(savedResponse.status()).toBe(200)
    const saved = (await savedResponse.json()).data
    expect(saved.status).toBe(index === 1 ? 'CANCELLED' : 'COMPLETED')
    expect(saved.action_config_snapshot).toEqual(created.action_config_snapshot)
    if (index === 0) {
      await page.getByRole('button', { name: '도움 사용 후기' }).click()
      await page.getByRole('radio', { name: '도움이 됐어요', exact: true }).check()
      await page.getByRole('button', { name: '후기 저장', exact: true }).click()
      await expect(page.getByText('저장된 후기: 도움이 됐어요', { exact: true })).toBeVisible()
      await page.reload()
      await page.getByRole('button', { name: '도움 사용 후기' }).click()
      await expect(page.getByText('저장된 후기: 도움이 됐어요', { exact: true })).toBeVisible()
      await page.getByRole('button', { name: '후기 수정하기' }).click()
      await page.getByRole('radio', { name: '도움이 되지 않았어요', exact: true }).check()
      await page.getByRole('button', { name: '후기 수정 저장' }).click()
      await expect(page.getByText('저장된 후기: 도움이 되지 않았어요', { exact: true })).toBeVisible()
      const feedbackResponse = await request.get(`${api}/api/v1/support-action-plans/${created.support_action_plan_id}/followups`, { headers })
      expect(feedbackResponse.status()).toBe(200)
      expect((await feedbackResponse.json()).data).toMatchObject({ response: 'NOT_HELPED', revision: 2, support_action_plan_id: created.support_action_plan_id })
    }
  }
})
