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
})
