import { apiRequest } from './client'

export type FeedbackRating = 'POSITIVE' | 'NEGATIVE'
export type FeedbackTarget = { guideId: string } | { sessionId: string; messageId: string }
export type FeedbackResponse = {
  data: { id: string; rating: FeedbackRating; created_at: string; updated_at: string }
}

function feedbackPath(target: FeedbackTarget) {
  return 'guideId' in target
    ? `/api/v1/guides/${encodeURIComponent(target.guideId)}/feedback`
    : `/api/v1/chat-sessions/${encodeURIComponent(target.sessionId)}/messages/${encodeURIComponent(target.messageId)}/feedback`
}

export function submitFeedback(target: FeedbackTarget, rating: FeedbackRating, comment: string) {
  return apiRequest<FeedbackResponse>(feedbackPath(target), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rating, comment: comment.trim() || null }),
  })
}

export function deleteFeedback(target: FeedbackTarget) {
  return apiRequest<void>(feedbackPath(target), { method: 'DELETE' })
}
