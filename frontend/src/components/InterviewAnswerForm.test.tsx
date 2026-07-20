import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { InterviewAnswerForm } from './InterviewAnswerForm'

const interrupt = { type: 'interview_answer' as const, target: 'interview_state' as const, question_id: 'q1', question: '介绍项目', accepted_actions: ['submit_answer', 'context_update', 'end_interview'] as ('submit_answer' | 'context_update' | 'end_interview')[] }
describe('InterviewAnswerForm', () => {
  it('submits answers and context with optional answer revision', async () => {
    const onSubmit = vi.fn(); const user = userEvent.setup(); render(<InterviewAnswerForm interrupt={interrupt} disabled={false} onSubmit={onSubmit} />)
    await user.type(screen.getByLabelText('回答'), '原始回答'); await user.click(screen.getByRole('button', { name: '提交回答' })); expect(onSubmit).toHaveBeenCalledWith({ action: 'submit_answer', answer: '原始回答' })
    await user.type(screen.getByLabelText('补充或更正背景'), '背景补充'); await user.click(screen.getByRole('button', { name: '提交补充' })); expect(onSubmit).toHaveBeenLastCalledWith({ action: 'context_update', context: '背景补充', answer: undefined })
    await user.click(screen.getByRole('checkbox')); await user.click(screen.getByRole('button', { name: '提交补充' })); expect(onSubmit).toHaveBeenLastCalledWith({ action: 'context_update', context: '背景补充', answer: '原始回答' })
  })
  it('confirms before ending the interview', async () => {
    const onSubmit = vi.fn(); const user = userEvent.setup(); vi.stubGlobal('confirm', vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true)); render(<InterviewAnswerForm interrupt={interrupt} disabled={false} onSubmit={onSubmit} />)
    await user.click(screen.getByRole('button', { name: '结束面试' })); expect(onSubmit).not.toHaveBeenCalled(); await user.click(screen.getByRole('button', { name: '结束面试' })); expect(onSubmit).toHaveBeenCalledWith({ action: 'end_interview' })
  })
})