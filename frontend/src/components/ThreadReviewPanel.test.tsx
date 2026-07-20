import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ThreadReviewPanel } from './ThreadReviewPanel'

const interrupt = { type: 'final_review' as const, target: 'jd_parsed' as const, draft: {}, accepted_actions: ['approve', 'reject'] as ('approve' | 'reject')[] }
describe('ThreadReviewPanel', () => {
  it('renders final review and disables every control while resuming', () => {
    render(<ThreadReviewPanel interrupt={interrupt} isResuming error={null} onResume={vi.fn()} />)
    expect(screen.getByRole('button', { name: '提交中...' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '驳回' })).toBeDisabled()
    expect(screen.getByLabelText('驳回反馈')).toBeDisabled()
  })
  it.each(['IDEMPOTENCY_KEY_REUSED', 'RESUME_IN_PROGRESS', 'CHECKPOINT_NOT_FOUND', 'HITL_COMMAND_INVALID', 'GRAPH_EXECUTION_FAILED'])('shows %s error', (code) => {
    render(<ThreadReviewPanel interrupt={interrupt} isResuming={false} error={{ code, message: '失败' }} onResume={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent(`${code}: 失败`)
  })
})