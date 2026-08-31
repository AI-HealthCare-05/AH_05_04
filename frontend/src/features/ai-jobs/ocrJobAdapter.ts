import type { OcrJobStatus } from '../../api/prescriptions'
import type { AiJobViewStatus } from './jobState'

export function adaptOcrJobStatus(status: OcrJobStatus): AiJobViewStatus {
  switch (status) {
    case 'PENDING':
      return 'PENDING'
    case 'PROCESSING':
      return 'PROCESSING'
    case 'COMPLETED':
      return 'COMPLETED'
    case 'FAILED':
      return 'FAILED'
  }
}
