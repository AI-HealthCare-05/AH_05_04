import { expect, test } from '@playwright/test'
import { ids, installRequirementsApi, syntheticToken } from './fixtures/requirementsApi'

for (const width of [320, 390, 412]) {
  test(`Guide feedback retries, changes and deletes at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await page.addInitScript(token => localStorage.setItem('access_token', token), syntheticToken)
    await installRequirementsApi(page, { existingPrescription: true, existingGuide: true })
    const submissions: unknown[] = []
    await page.route('**/api/v1/guides/*/feedback', async route => {
      if (route.request().method() === 'DELETE') { await route.fulfill({ status: 204 }); return }
      submissions.push(route.request().postDataJSON())
      if (submissions.length === 1) { await route.fulfill({ status: 503, json: { message: 'synthetic error' } }); return }
      await route.fulfill({ status: 200, json: { data: { id: 'synthetic-feedback', rating: route.request().postDataJSON().rating } } })
    })
    await page.goto(`/guides/${ids.guide}`)
    const feedback = page.getByRole('region', { name: '답변 피드백' })
    await feedback.getByRole('button', { name: '👎 아쉬워요' }).focus()
    await page.keyboard.press('Enter')
    await feedback.getByLabel('의견 (선택)').fill('합성 평가: 안내가 반복돼요.')
    await feedback.getByRole('button', { name: '피드백 보내기' }).click()
    await expect(feedback.getByRole('alert')).toBeVisible()
    await expect(feedback.getByLabel('의견 (선택)')).toHaveValue('합성 평가: 안내가 반복돼요.')
    await feedback.getByRole('button', { name: '피드백 보내기' }).click()
    await expect(feedback.getByRole('status')).toBeVisible()
    await feedback.getByRole('button', { name: '👍 도움이 됐어요' }).click()
    await feedback.getByRole('button', { name: '피드백 보내기' }).click()
    await expect(feedback.getByText('저장된 평가: 도움이 됐어요')).toBeVisible()
    expect(submissions).toHaveLength(3)
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await feedback.evaluate(element => element.scrollIntoView({ block: 'center' }))
    await feedback.screenshot({ path: `test-results/feedback-${width}.png` })
    await feedback.getByRole('button', { name: '피드백 삭제' }).click()
    await expect(feedback.getByText('피드백을 삭제했어요.')).toBeVisible()
    await page.reload()
    await expect(feedback.getByLabel('의견 (선택)')).toHaveCount(0)
  })
}

test('Chat exposes feedback only for a completed assistant response', async ({ page }) => {
  await page.addInitScript(token => localStorage.setItem('access_token', token), syntheticToken)
  await installRequirementsApi(page, { existingPrescription: true, existingChat: true })
  let submitted = ''
  await page.route('**/api/v1/chat-sessions/*/messages/*/feedback', async route => {
    submitted = route.request().url()
    await route.fulfill({ status: 201, json: { data: { id: 'synthetic-feedback', rating: 'POSITIVE' } } })
  })
  await page.goto(`/chat?prescription_id=${ids.prescription}`)
  await expect(page.getByRole('region', { name: '답변 피드백' })).toHaveCount(0)
  await page.getByLabel('복약 질문').fill('합성 질문입니다.')
  await page.getByRole('button', { name: '질문 전송' }).click()
  const feedback = page.getByRole('region', { name: '답변 피드백' })
  await expect(feedback).toHaveCount(1)
  await feedback.getByRole('button', { name: '👍 도움이 됐어요' }).click()
  await feedback.getByRole('button', { name: '피드백 보내기' }).click()
  await expect(feedback.getByRole('status')).toBeVisible()
  expect(submitted).toContain('/chat-sessions/')
  expect(submitted).toMatch(/\/messages\/[^/]+\/feedback$/)
})
