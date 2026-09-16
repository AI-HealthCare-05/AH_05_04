import { expect, test } from '@playwright/test'

const viewports = [320, 390, 412] as const

test('미승인 약관 초안은 320/390/412px에서 확정 필수 동의와 가입을 차단한다', async ({ page }) => {
  const mutations: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/') &&
      !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request.url())
  })

  for (const width of viewports) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/signup')

    const terms = page.getByRole('checkbox', { name: '필수 약관 승인 대기 중' })
    await expect(terms).toBeDisabled()
    await expect(terms).not.toBeChecked()
    await expect(page.getByRole('button', { name: '가입 완료' })).toBeDisabled()
    await expect(page.getByText('최종 법무/Privacy 승인 후 동의할 수 있습니다')).toBeVisible()
    await expect(page.getByText('필수 약관에 동의합니다')).toHaveCount(0)

    await page.getByRole('button', { name: '약관 보기' }).click()
    await expect(page.getByRole('heading', { name: '필수 약관 검토본' })).toBeFocused()
    await expect(page.getByRole('note', { name: '승인 전 초안 · 검토용' })).toBeVisible()
    await expect(page.getByText('최종 시행 약관')).toHaveCount(0)
    await expect(page.getByText(/회사명이 제공하는/)).toBeVisible()
    await expect(page.getByText(/\[관할 법원 – 예: 회사 소재지 관할 법원\]/)).toBeAttached()

    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByRole('button', { name: '이전 화면' }).click()
  }

  expect(mutations).toEqual([])
})
