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
  a: ({ children, href, title }) => (
    <span>
      {children}
      {href ? ` (${href})` : null}
      {title ? ` (${title})` : null}
    </span>
  ),
  img: ({ alt, src, title }) => (
    <span>
      {alt}
      {src ? ` (${src})` : null}
      {title ? ` (${title})` : null}
    </span>
  ),
}

const referenceDefinitionPattern =
  /^[\t ]{0,3}\[(?:\\(?:[^\r\n]|\r?\n(?=[\t ]*\S))|[^\]\\\r\n]|\r?\n(?=[\t ]*\S)){1,999}\]:/m
const fencedCodePattern = /^[\t ]{0,3}(?:`{3,}|~{3,})/m

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
  if (
    referenceDefinitionPattern.test(content) ||
    fencedCodePattern.test(content)
  ) {
    return <span className="chat-message__plain-fallback">{content}</span>
  }

  return (
    <MarkdownFallbackBoundary key={content} content={content}>
      <div className="chat-message__markdown">
        <ReactMarkdown
          allowedElements={[...allowedMarkdownElements]}
          components={unsupportedMarkdownText}
          unwrapDisallowed
          // Links and images stay as inert text, so preserve their original URLs without the default sanitize transform.
          urlTransform={(url) => url}
        >
          {content}
        </ReactMarkdown>
      </div>
    </MarkdownFallbackBoundary>
  )
}
