import { Component, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'

const allowedMarkdownElements = [
  'p',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'ul',
  'ol',
  'li',
  'strong',
  'em',
  'br',
  'a',
  'img',
] as const

const unsupportedMarkdownText: Components = {
  a: ({ children, href }) => (
    <span>
      {children}
      {href ? ` (${href})` : null}
    </span>
  ),
  img: ({ alt, src }) => (
    <span>
      {alt}
      {src ? ` (${src})` : null}
    </span>
  ),
}

type MarkdownFallbackBoundaryProps = {
  children: ReactNode
  content: string
}

type MarkdownFallbackBoundaryState = {
  failed: boolean
}

export class MarkdownFallbackBoundary extends Component<
  MarkdownFallbackBoundaryProps,
  MarkdownFallbackBoundaryState
> {
  state: MarkdownFallbackBoundaryState = { failed: false }

  static getDerivedStateFromError(): MarkdownFallbackBoundaryState {
    return { failed: true }
  }

  render() {
    if (this.state.failed) {
      return (
        <span className="chat-message__plain-fallback">
          {this.props.content}
        </span>
      )
    }

    return this.props.children
  }
}

export function AssistantMessageContent({ content }: { content: string }) {
  return (
    <MarkdownFallbackBoundary key={content} content={content}>
      <div className="chat-message__markdown">
        <ReactMarkdown
          allowedElements={[...allowedMarkdownElements]}
          components={unsupportedMarkdownText}
          unwrapDisallowed
        >
          {content}
        </ReactMarkdown>
      </div>
    </MarkdownFallbackBoundary>
  )
}
