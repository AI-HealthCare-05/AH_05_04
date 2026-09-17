import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ResponseFeedback } from '../src/components/ResponseFeedback'
import { deleteFeedback, submitFeedback } from '../src/api/feedback'

vi.mock('../src/api/feedback', () => ({ submitFeedback: vi.fn(), deleteFeedback: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

describe('ResponseFeedback', () => {
  it('keeps draft after failure, retries, changes rating and deletes without persistence', async () => {
    const target = { guideId: 'synthetic-guide' }
    vi.mocked(submitFeedback).mockRejectedValueOnce(new Error('synthetic failure')).mockResolvedValue({
      data: { id: 'feedback', rating: 'NEGATIVE', created_at: '2026-09-16', updated_at: '2026-09-16' },
    })
    vi.mocked(deleteFeedback).mockResolvedValue(undefined)
    render(<ResponseFeedback target={target} />)
    expect(screen.queryByLabelText('의견 (선택)')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '👎 아쉬워요' }))
    fireEvent.change(screen.getByLabelText('의견 (선택)'), { target: { value: 'synthetic comment' } })
    fireEvent.click(screen.getByRole('button', { name: '피드백 보내기' }))
    await screen.findByRole('alert')
    expect((screen.getByLabelText('의견 (선택)') as HTMLTextAreaElement).value).toBe('synthetic comment')
    expect(screen.queryByText('저장된 평가: 아쉬워요')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '피드백 보내기' }))
    await screen.findByRole('status')
    expect(submitFeedback).toHaveBeenLastCalledWith(target, 'NEGATIVE', 'synthetic comment')
    fireEvent.click(screen.getByRole('button', { name: '👍 도움이 됐어요' }))
    fireEvent.click(screen.getByRole('button', { name: '피드백 보내기' }))
    await waitFor(() => expect(submitFeedback).toHaveBeenCalledTimes(3))
    await screen.findByRole('status')
    expect(submitFeedback).toHaveBeenLastCalledWith(target, 'POSITIVE', 'synthetic comment')
    fireEvent.click(screen.getByRole('button', { name: '피드백 삭제' }))
    await screen.findByText('피드백을 삭제했어요.')
    expect(deleteFeedback).toHaveBeenCalledWith(target)
    expect(screen.queryByLabelText('의견 (선택)')).toBeNull()
    expect(JSON.stringify(localStorage)).not.toContain('synthetic comment')
  })

  it('blocks duplicate submissions while request is pending', async () => {
    let complete!: (value: Awaited<ReturnType<typeof submitFeedback>>) => void
    vi.mocked(submitFeedback).mockImplementation(() => new Promise(resolve => { complete = resolve }))
    render(<ResponseFeedback target={{ sessionId: 'session', messageId: 'message' }} />)
    fireEvent.click(screen.getByRole('button', { name: '👍 도움이 됐어요' }))
    const form = screen.getByRole('button', { name: '피드백 보내기' }).closest('form')!
    fireEvent.submit(form)
    fireEvent.submit(form)
    expect(submitFeedback).toHaveBeenCalledTimes(1)
    expect((screen.getByRole('button', { name: '👎 아쉬워요' }) as HTMLButtonElement).disabled).toBe(true)
    await act(async () => complete({ data: { id: 'feedback', rating: 'POSITIVE', created_at: '', updated_at: '' } }))
    await screen.findByRole('status')
  })

  it('resets an unsaved draft on target change and keeps pending requests bound to their original target', async () => {
    let completeFirst!: (value: Awaited<ReturnType<typeof submitFeedback>>) => void
    let completeSecond!: (value: Awaited<ReturnType<typeof submitFeedback>>) => void
    vi.mocked(submitFeedback)
      .mockImplementationOnce(() => new Promise(resolve => { completeFirst = resolve }))
      .mockImplementationOnce(() => new Promise(resolve => { completeSecond = resolve }))
    const draftTarget = { sessionId: 'session', messageId: 'draft-message' }
    const firstRequestTarget = { sessionId: 'session', messageId: 'message-1' }
    const secondRequestTarget = { sessionId: 'session', messageId: 'message-2' }
    const { rerender } = render(<ResponseFeedback target={draftTarget} />)

    fireEvent.click(screen.getByRole('button', { name: '👎 아쉬워요' }))
    fireEvent.change(screen.getByLabelText('의견 (선택)'), {
      target: { value: '이전 target의 미제출 입력' },
    })
    rerender(<ResponseFeedback target={firstRequestTarget} />)
    await waitFor(() =>
      expect(screen.queryByLabelText('의견 (선택)')).toBeNull(),
    )
    expect(submitFeedback).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '👎 아쉬워요' }))
    fireEvent.change(screen.getByLabelText('의견 (선택)'), {
      target: { value: '첫 번째 요청 입력' },
    })
    fireEvent.click(screen.getByRole('button', { name: '피드백 보내기' }))
    expect(submitFeedback).toHaveBeenNthCalledWith(1, firstRequestTarget, 'NEGATIVE', '첫 번째 요청 입력')

    rerender(<ResponseFeedback target={secondRequestTarget} />)
    await waitFor(() =>
      expect(screen.queryByLabelText('의견 (선택)')).toBeNull(),
    )
    expect(screen.queryByRole('status')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '👍 도움이 됐어요' }))
    expect(screen.getByLabelText('의견 (선택)')).toHaveProperty('value', '')
    fireEvent.change(screen.getByLabelText('의견 (선택)'), {
      target: { value: '새 target 입력' },
    })
    fireEvent.click(screen.getByRole('button', { name: '피드백 보내기' }))

    expect(submitFeedback).toHaveBeenNthCalledWith(
      2,
      secondRequestTarget,
      'POSITIVE',
      '새 target 입력',
    )

    await act(async () => completeFirst({ data: { id: 'old-feedback', rating: 'NEGATIVE', created_at: '', updated_at: '' } }))
    expect(screen.queryByRole('status')).toBeNull()

    await act(async () => completeSecond({ data: { id: 'new-feedback', rating: 'POSITIVE', created_at: '', updated_at: '' } }))
    expect(await screen.findByText('저장된 평가: 도움이 됐어요')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('피드백을 저장했어요.')
  })
})
