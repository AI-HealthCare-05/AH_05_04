import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import DevPreviewPage from '../src/dev-preview/DevPreviewPage'
import { previewScenarioIds } from '../src/dev-preview/previewFixtures'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

function renderPreview(screenName: string, scenario: string) {
  return render(
    <MemoryRouter
      initialEntries={[
        `/dev/preview?screen=${screenName}&scenario=${scenario}`,
      ]}
    >
      <LocationProbe />
      <Routes>
        <Route path="/dev/preview" element={<DevPreviewPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: vi.fn(() => 'blob:synthetic-preview-document'),
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: vi.fn(),
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('DevPreviewPage', () => {
  it('Preview 경고와 화면/scenario 선택기를 표시한다', async () => {
    renderPreview('prescription-review', 'normal')

    expect(screen.getByText('DEV PREVIEW')).toBeTruthy()
    expect(screen.getByText('Mock data only')).toBeTruthy()
    expect(screen.getByText('실제 API/DB 동작 검증용이 아님')).toBeTruthy()
    expect(screen.getByLabelText('Preview 화면')).toBeTruthy()
    expect(screen.getByLabelText('Preview scenario')).toBeTruthy()
    expect(await screen.findByText('처방전과 같은지 확인해 주세요')).toBeTruthy()
  })

  it.each([
    ['normal', '처방전과 같은지 확인해 주세요'],
    ['missing-required', '필수 처방 항목이 누락됐어요'],
    ['optional-empty', '약 1/1개 검토 완료'],
    ['missing-medication', '약 이름을 인식하지 못했어요'],
    ['missing-required-edit', '누락된 항목을 직접 입력해 주세요'],
    ['review-in-progress', '처방전을 인식하고 있어요'],
    ['completed-before-ack', '원본 처방전의 모든 항목을 직접 확인했습니다.'],
    ['completed', '이미 확정된 처방이에요'],
    ['validation-error', '필수값 1개 누락'],
    ['manual-add-form', '약물 추가'],
    ['manual-add-validation', '약물이름을 입력해 주세요.'],
    ['manual-add-success', '수동 추가 약'],
  ])('Prescription Review %s scenario를 렌더링한다', async (scenario, expectedText) => {
    renderPreview('prescription-review', scenario)

    expect(await screen.findByText(expectedText, { exact: false })).toBeTruthy()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('manual-add-success는 수동 약물을 기존 검토 진행률에 포함한다', async () => {
    renderPreview('prescription-review', 'manual-add-success')
    expect(await screen.findByText('수동 추가 약', { exact: false })).toBeTruthy()
    expect(screen.getByText('약 1/2개 검토 완료')).toBeTruthy()
    expect(screen.getByText('검토 전')).toBeTruthy()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    ['completed', '가이드 전체 내용'],
    ['structured', '확인된 약 목록 · 1개'],
    ['loading', '복약 가이드를 불러오고 있어요'],
    ['empty', '가이드 내용이 아직 없어요'],
    ['failed', '가이드를 만들지 못했어요'],
  ])('Guide %s scenario를 렌더링한다', async (scenario, expectedText) => {
    renderPreview('guide', scenario)

    expect(await screen.findByText(expectedText, { exact: false })).toBeTruthy()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    ['no-prescription', '먼저 처방전을 등록해 주세요'],
    ['existing-messages', '아침 약은 언제 먹나요?'],
    ['input-ready', '궁금한 내용을 입력하세요'],
    ['generating', '답변을 확인하고 있어요'],
    ['long-answer', '확정된 처방에 표시된 약 이름'],
    ['error', '네트워크 연결을 확인한 뒤 다시 시도해 주세요.'],
  ])('Chat %s scenario를 렌더링한다', async (scenario, expectedText) => {
    renderPreview('chat', scenario)

    if (scenario === 'input-ready') {
      expect(await screen.findByPlaceholderText(expectedText)).toBeTruthy()
    } else {
      expect(await screen.findByText(expectedText, { exact: false })).toBeTruthy()
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('화면을 바꾸면 해당 화면의 첫 scenario를 선택한다', async () => {
    renderPreview('prescription-review', 'normal')

    fireEvent.change(screen.getByLabelText('Preview 화면'), {
      target: { value: 'guide' },
    })

    await waitFor(() => {
      const scenarioSelect = screen.getByLabelText(
        'Preview scenario',
      ) as HTMLSelectElement
      expect(scenarioSelect.value).toBe(previewScenarioIds.guide[0])
    })
    expect(await screen.findByText('가이드 전체 내용')).toBeTruthy()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    ['prescription-review', 'missing-required', '다시 업로드하기'],
    ['guide', 'completed', '복약 챗봇 도지와 이야기하기'],
    ['chat', 'no-prescription', '처방전 등록하기'],
  ])(
    '%s Preview의 %s CTA가 실제 제품 route로 이동하지 않는다',
    async (screenName, scenario, buttonName) => {
      renderPreview(screenName, scenario)
      const expectedLocation = `/dev/preview?screen=${screenName}&scenario=${scenario}`

      fireEvent.click(
        await screen.findByRole('button', { name: buttonName }),
      )

      expect(screen.getByTestId('location').textContent).toBe(expectedLocation)
      expect(fetchMock).not.toHaveBeenCalled()
    },
  )
})
