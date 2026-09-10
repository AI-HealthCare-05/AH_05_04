import { cleanup, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  AssistantMessageContent,
  MarkdownFallbackBoundary,
} from '../src/pages/AssistantMessageContent'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('AssistantMessageContent', () => {
  it('plain text를 내용 변경 없이 표시한다', () => {
    render(<AssistantMessageContent content="식후에 복용하세요." />)

    expect(screen.getByText('식후에 복용하세요.')).toBeTruthy()
  })

  it('여러 paragraph를 별도 단락으로 표시한다', () => {
    const { container } = render(
      <AssistantMessageContent content={'첫 번째 단락입니다.\n\n두 번째 단락입니다.'} />,
    )

    expect(container.querySelectorAll('p')).toHaveLength(2)
  })

  it('heading을 과도한 시각 단계 추가 없이 구조화한다', () => {
    render(<AssistantMessageContent content="## 복용 안내" />)

    expect(screen.getByRole('heading', { level: 2, name: '복용 안내' })).toBeTruthy()
  })

  it('unordered list를 목록으로 표시한다', () => {
    render(<AssistantMessageContent content={'- 아침 복용\n- 저녁 복용'} />)

    expect(screen.getByRole('list').tagName).toBe('UL')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('ordered list를 순서 목록으로 표시한다', () => {
    render(<AssistantMessageContent content={'1. 처방전 확인\n2. 약 봉투 확인'} />)

    expect(screen.getByRole('list').tagName).toBe('OL')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('strong과 emphasis를 인라인 구조로 표시한다', () => {
    const { container } = render(
      <AssistantMessageContent content="**확인된 내용**과 *참고 내용*" />,
    )

    expect(container.querySelector('strong')?.textContent).toBe('확인된 내용')
    expect(container.querySelector('em')?.textContent).toBe('참고 내용')
  })

  it('Markdown hard break를 line break로 표시한다', () => {
    const { container } = render(
      <AssistantMessageContent content={'첫째 줄  \n둘째 줄'} />,
    )

    expect(container.querySelector('br')).toBeTruthy()
  })

  it('heading, paragraph, list 혼합 구조를 순서대로 표시한다', () => {
    const { container } = render(
      <AssistantMessageContent
        content={'### 확인 사항\n\n원문 내용입니다.\n\n- 항목 A\n- 항목 B'}
      />,
    )

    expect(
      Array.from(container.querySelectorAll('h3, p, ul')).map(
        (element) => element.tagName,
      ),
    ).toEqual(['H3', 'P', 'UL'])
  })

  it('공백 없는 긴 문자열도 원문을 보존한다', () => {
    const longText = '긴문자열'.repeat(80)
    const { container } = render(
      <AssistantMessageContent content={longText} />,
    )

    expect(container.textContent).toBe(longText)
    expect(container.firstElementChild?.classList).toContain(
      'chat-message__markdown',
    )
  })

  it('malformed Markdown도 유실 없이 text로 표시한다', () => {
    const content = '**닫히지 않은 강조와 [링크(https://example.invalid'
    const { container } = render(
      <AssistantMessageContent content={content} />,
    )

    expect(container.textContent).toBe(content)
  })

  it('link를 활성 링크로 만들지 않고 label과 URL을 모두 표시한다', () => {
    const { container } = render(
      <AssistantMessageContent content="[복약 안내](https://example.invalid/guide)" />,
    )

    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).toContain('복약 안내')
    expect(container.textContent).toContain('https://example.invalid/guide')
  })

  it('image를 로드하지 않고 alt text와 URL을 모두 표시한다', () => {
    const { container } = render(
      <AssistantMessageContent content="![약 봉투 설명](https://example.invalid/medicine.png)" />,
    )

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('약 봉투 설명')
    expect(container.textContent).toContain('https://example.invalid/medicine.png')
  })

  it('inline code의 text를 누락 없이 표시한다', () => {
    const { container } = render(
      <AssistantMessageContent content="`하루 2회` 복용하세요." />,
    )

    expect(container.querySelector('code')).toBeNull()
    expect(container.textContent).toContain('하루 2회 복용하세요.')
  })

  it('fenced code block의 모든 줄을 누락 없이 표시한다', () => {
    const { container } = render(
      <AssistantMessageContent content={'```text\n첫째 줄\n둘째 줄\n```'} />,
    )

    expect(container.querySelector('pre')).toBeNull()
    expect(container.querySelector('code')).toBeNull()
    expect(container.textContent).toContain('첫째 줄')
    expect(container.textContent).toContain('둘째 줄')
  })

  it('raw HTML과 script를 DOM으로 실행하지 않고 문자열로 표시한다', () => {
    const content = '<strong>HTML 강조</strong>\n<script>window.hacked = true</script>'
    const { container } = render(
      <AssistantMessageContent content={content} />,
    )

    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('strong')).toBeNull()
    expect(container.textContent).toContain('<strong>HTML 강조</strong>')
    expect(container.textContent).toContain('<script>window.hacked = true</script>')
    expect('hacked' in window).toBe(false)
  })

  it('renderer에서 예외가 발생하면 전체 원문을 plain text로 표시한다', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)

    function BrokenRenderer(): never {
      throw new Error('synthetic renderer failure')
    }

    render(
      <MarkdownFallbackBoundary content="원문 **그대로**">
        <BrokenRenderer />
      </MarkdownFallbackBoundary>,
    )

    expect(screen.getByText('원문 **그대로**')).toBeTruthy()
  })
})
