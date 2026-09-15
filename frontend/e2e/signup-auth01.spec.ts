import { expect, test } from '@playwright/test'
import { installRequirementsApi } from './fixtures/requirementsApi'

const viewports = [320, 390, 412] as const

test('AUTH-01 responsive layout, accessibility and consent payload contract', async ({ page }) => {
  const api = await installRequirementsApi(page)

  for (const width of viewports) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/signup')

    const optionalConsent = page.getByRole('checkbox', { name: /기능 이용 선택 동의/ })
    await expect(page.getByRole('checkbox')).toHaveCount(2)
    await expect(page.getByRole('button', { name: '중복확인' })).toHaveCount(0)
    await expect(optionalConsent).not.toBeChecked()

    const layout = await page.evaluate(() => {
      const horizontalOverflow = document.documentElement.scrollWidth - window.innerWidth
      const controls = Array.from(document.querySelectorAll<HTMLElement>('input, form button'))
      const clippedControls = controls.filter((control) => {
        const bounds = control.getBoundingClientRect()
        return bounds.left < 0 || bounds.right > window.innerWidth
      }).map((control) => control.id || control.textContent?.trim() || control.tagName)
      const individualConsentCards = document.querySelectorAll('.mvp-signup-consents__option').length
      return { horizontalOverflow, clippedControls, individualConsentCards }
    })
    expect(layout).toEqual({ horizontalOverflow: 0, clippedControls: [], individualConsentCards: 1 })

    const email = page.getByLabel('이메일', { exact: true })
    await email.focus()
    await email.pressSequentially('d')
    await expect(email).toBeFocused()
    await expect(email).toHaveValue('d')

    await page.locator('form').evaluate((form) => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })
    const requiredTerms = page.getByRole('checkbox', { name: '필수 약관에 동의합니다' })
    await expect(requiredTerms).toBeFocused()
    await expect(requiredTerms).toHaveAttribute('aria-invalid', 'true')
    await expect(page.getByText('필수 약관에 동의해 주세요.')).toBeVisible()

    await page.getByLabel('이름').fill('합성 사용자')
    await email.fill(`auth01-${width}@example.com`)
    await page.getByLabel('비밀번호').fill('Synthetic1!')
    await requiredTerms.check()

    if (width === 390) {
      const geometry = await page.evaluate(() => {
        const size = (selector: string) => {
          const bounds = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect()
          return { width: bounds.width, height: bounds.height }
        }
        return {
          name: size('#signup-name'),
          password: size('#signup-password'),
          required: size('.mvp-signup-required'),
          optional: size('.mvp-signup-consents__option'),
          submit: size('.mvp-signup-submit'),
        }
      })
      expect(geometry).toEqual({
        name: { width: 350, height: 48 },
        password: { width: 350, height: 48 },
        required: { width: 350, height: 86 },
        optional: { width: 350, height: 86 },
        submit: { width: 350, height: 52 },
      })

      await page.locator('body').click({ position: { x: 1, y: 1 } })
      for (let step = 0; step < 10; step += 1) {
        await page.keyboard.press('Tab')
        if (await optionalConsent.evaluate((element) => element === document.activeElement)) break
      }
      await expect(optionalConsent).toBeFocused()
      expect(await optionalConsent.evaluate((element) => element.matches(':focus-visible'))).toBe(true)
      await page.keyboard.press('Space')
      await expect(optionalConsent).toBeChecked()
    }

    await page.screenshot({ path: `test-results/requirements/signup-auth01-${width}.png`, fullPage: true })
    await page.getByRole('button', { name: '가입 완료' }).click()
    await expect(page).toHaveURL(/\/login$/)
  }

  expect(api.signupRequests).toEqual([
    {
      name: '합성 사용자',
      email: 'auth01-320@example.com',
      password: 'Synthetic1!',
      consents: [],
    },
    {
      name: '합성 사용자',
      email: 'auth01-390@example.com',
      password: 'Synthetic1!',
      consents: [
        { purpose: 'OCR' },
        { purpose: 'GUIDE' },
        { purpose: 'CHAT' },
        { purpose: 'NOTIFICATION' },
      ],
    },
    {
      name: '합성 사용자',
      email: 'auth01-412@example.com',
      password: 'Synthetic1!',
      consents: [],
    },
  ])
  expect(api.unexpectedRequests).toEqual([])
})
