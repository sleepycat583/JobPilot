import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EvaluationUnavailableForm } from './EvaluationUnavailableForm'

const interrupt = { type: 'interview_evaluation_unavailable' as const, target: 'question_record' as const, question_id: 'q1', accepted_actions: ['retry_evaluation', 'skip_evaluation'] as ('retry_evaluation' | 'skip_evaluation')[] }
describe('EvaluationUnavailableForm', () => {
  it('submits retry and confirmed skip commands', async () => {
    const onSubmit = vi.fn(); const user = userEvent.setup(); vi.stubGlobal('confirm', vi.fn().mockReturnValue(true)); render(<EvaluationUnavailableForm interrupt={interrupt} disabled={false} onSubmit={onSubmit} />)
    await user.click(screen.getByRole('button', { name: '重新评价' })); expect(onSubmit).toHaveBeenCalledWith({ action: 'retry_evaluation' }); await user.click(screen.getByRole('button', { name: '跳过评价' })); expect(onSubmit).toHaveBeenCalledWith({ action: 'skip_evaluation' })
  })
  it('does not submit when skip is cancelled', async () => {
    const onSubmit = vi.fn(); const user = userEvent.setup(); vi.stubGlobal('confirm', vi.fn().mockReturnValue(false)); render(<EvaluationUnavailableForm interrupt={interrupt} disabled={false} onSubmit={onSubmit} />)
    await user.click(screen.getByRole('button', { name: '跳过评价' })); expect(onSubmit).not.toHaveBeenCalled()
  })
})