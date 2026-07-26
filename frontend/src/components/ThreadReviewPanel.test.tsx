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
  it('renders the frozen JD draft fields before approval', () => {
    const jdInterrupt = { ...interrupt, draft: { job_title: 'Python 后端工程师', seniority: 'mid', company_name: null, responsibilities: ['负责接口设计'], skills: [{ name: 'Python' }], experience_requirements: ['3 年经验'], education_requirements: ['本科'], ambiguities: [] } }
    render(<ThreadReviewPanel interrupt={jdInterrupt} isResuming={false} error={null} onResume={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'JD 解析草稿' })).toBeInTheDocument()
    expect(screen.getByText('Python 后端工程师')).toBeInTheDocument()
    expect(screen.getByText('负责接口设计')).toBeInTheDocument()
    expect(screen.getByText('Python')).toBeInTheDocument()
  })
  it('identifies a match result final review', () => {
    render(<ThreadReviewPanel interrupt={{ ...interrupt, target: 'match_result' }} isResuming={false} error={null} onResume={vi.fn()} />)
    expect(screen.getByRole('heading', { name: '审核简历匹配结果' })).toBeInTheDocument()
  })
  it.each(['IDEMPOTENCY_KEY_REUSED', 'RESUME_IN_PROGRESS', 'CHECKPOINT_NOT_FOUND', 'HITL_COMMAND_INVALID', 'GRAPH_EXECUTION_FAILED'])('shows %s error', (code) => {
    render(<ThreadReviewPanel interrupt={interrupt} isResuming={false} error={{ code, message: '失败' }} onResume={vi.fn()} />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })
  it('shows differentiated recovery actions for key and in-progress errors', () => {
    const onRetry = vi.fn(); const onRefresh = vi.fn()
    const { rerender } = render(<ThreadReviewPanel interrupt={interrupt} isResuming={false} error={{ code: 'IDEMPOTENCY_KEY_REUSED', message: '失败' }} onResume={vi.fn()} onRetry={onRetry} onRefresh={onRefresh} />)
    expect(screen.getByText('当前表单提交参数与已使用的幂等键不一致')).toBeInTheDocument(); screen.getByRole('button', { name: '刷新当前状态' }).click(); expect(onRefresh).toHaveBeenCalled()
    rerender(<ThreadReviewPanel interrupt={interrupt} isResuming={false} error={{ code: 'RESUME_IN_PROGRESS', message: '失败' }} onResume={vi.fn()} onRetry={onRetry} onRefresh={onRefresh} />)
    screen.getByRole('button', { name: '重试' }).click(); expect(onRetry).toHaveBeenCalled()
  })
  it('offers refresh for a missing checkpoint so the parent can clear the panel', () => {
    const onRefresh = vi.fn()
    render(<ThreadReviewPanel interrupt={interrupt} isResuming={false} error={{ code: 'CHECKPOINT_NOT_FOUND', message: '不存在' }} onResume={vi.fn()} onRefresh={onRefresh} />)
    expect(screen.getByText('该审核已结束或线程不存在')).toBeInTheDocument()
    screen.getByRole('button', { name: '刷新当前状态' }).click()
    expect(onRefresh).toHaveBeenCalledOnce()
  })
})