import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { LowScoreReviewForm } from './LowScoreReviewForm'

const interrupt = { type: 'low_match_score' as const, target: 'match_result' as const, score: 30, threshold: 60, top_gaps: ['Java'], accepted_actions: ['continue', 'revise_inputs', 'cancel'] as ('continue' | 'revise_inputs' | 'cancel')[] }
describe('LowScoreReviewForm', () => {
  it('submits continue and cancel without feedback', async () => {
    const onSubmit = vi.fn(); const user = userEvent.setup(); render(<LowScoreReviewForm interrupt={interrupt} disabled={false} onSubmit={onSubmit} />)
    await user.click(screen.getByRole('button', { name: '继续' })); await user.click(screen.getByRole('button', { name: '取消' }))
    expect(onSubmit).toHaveBeenNthCalledWith(1, { action: 'continue' }); expect(onSubmit).toHaveBeenNthCalledWith(2, { action: 'cancel' })
  })
  it('submits optional cancel feedback when provided', async () => {
    const onSubmit = vi.fn(); const user = userEvent.setup(); render(<LowScoreReviewForm interrupt={interrupt} disabled={false} onSubmit={onSubmit} />)
    await user.type(screen.getByLabelText('取消反馈（可选）'), '暂不继续匹配'); await user.click(screen.getByRole('button', { name: '取消' }))
    expect(onSubmit).toHaveBeenCalledWith({ action: 'cancel', feedback: '暂不继续匹配' })
  })
  it('requires one valid revision input after entering the second step', async () => {
    const onSubmit = vi.fn(); const user = userEvent.setup(); render(<LowScoreReviewForm interrupt={interrupt} disabled={false} onSubmit={onSubmit} />)
    await user.click(screen.getByRole('button', { name: '修改输入后重新评审' })); const submit = screen.getByRole('button', { name: '提交修订' }); expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText('简历版本'), '2026-v2'); expect(submit).toBeEnabled(); await user.click(submit)
    expect(onSubmit).toHaveBeenCalledWith({ action: 'revise_inputs', feedback: undefined, resume_version: '2026-v2', jd_text: undefined })
  })
  it('accepts feedback-only revisions', async () => {
    const onSubmit = vi.fn(); const user = userEvent.setup(); render(<LowScoreReviewForm interrupt={interrupt} disabled={false} onSubmit={onSubmit} />)
    await user.click(screen.getByRole('button', { name: '修改输入后重新评审' })); const submit = screen.getByRole('button', { name: '提交修订' }); expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText('修订反馈'), '请重新检查项目经验'); expect(submit).toBeEnabled(); await user.click(submit)
    expect(onSubmit).toHaveBeenCalledWith({ action: 'revise_inputs', feedback: '请重新检查项目经验', resume_version: undefined, jd_text: undefined })
  })
  it('requires at least twenty characters for JD-only revisions', async () => {
    const onSubmit = vi.fn(); const user = userEvent.setup(); render(<LowScoreReviewForm interrupt={interrupt} disabled={false} onSubmit={onSubmit} />)
    await user.click(screen.getByRole('button', { name: '修改输入后重新评审' })); const submit = screen.getByRole('button', { name: '提交修订' })
    await user.type(screen.getByLabelText('修正后的 JD'), '太短'); expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText('修正后的 JD'), '的岗位描述，要求具备稳定的后端开发经验。'); expect(submit).toBeEnabled(); await user.click(submit)
    expect(onSubmit).toHaveBeenCalledWith({ action: 'revise_inputs', feedback: undefined, resume_version: undefined, jd_text: '太短的岗位描述，要求具备稳定的后端开发经验。' })
  })
})