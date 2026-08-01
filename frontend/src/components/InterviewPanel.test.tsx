import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { InterviewPanel } from './InterviewPanel'
import type { InterviewState } from '../types'

const interview: InterviewState = {
  status: 'waiting', target_question_count: 8, current_question_id: 'q2', user_context_updates: [], report: null,
  question_records: [
    { question_id: 'q1', topic: '基础', question: '第一题', answer: '第一题回答', follow_up_of: null, scores: null, feedback: null, strengths: [], issues: [] },
    { question_id: 'q2', topic: '项目', question: '第二题', answer: '', follow_up_of: null, scores: null, feedback: null, strengths: [], issues: [] },
  ],
}

const answerInterrupt = { type: 'interview_answer' as const, target: 'interview_state' as const, question_id: 'q2', question: '第二题', accepted_actions: ['submit_answer', 'context_update', 'end_interview'] as ('submit_answer' | 'context_update' | 'end_interview')[] }

describe('InterviewPanel', () => {
  it('shows one question at a time and preserves the current answer draft while navigating', async () => {
    const user = userEvent.setup()
    render(<InterviewPanel interview={interview} answerInterrupt={answerInterrupt} onResume={vi.fn()} />)

    expect(screen.getByRole('heading', { name: '第二题' })).toBeInTheDocument()
    expect(screen.queryByText('第一题回答')).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('回答'), '第二题草稿')
    await user.click(screen.getByRole('button', { name: '上一题' }))
    expect(screen.getByText('第一题回答')).toBeInTheDocument()
    expect(screen.queryByLabelText('回答')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '下一题' }))
    expect(screen.getByLabelText('回答')).toHaveValue('第二题草稿')
    expect(screen.getByText('第 2 / 8 题 · 已完成 1 题')).toBeInTheDocument()
  })

  it('disables navigation at the generated question boundaries', () => {
    render(<InterviewPanel interview={{ ...interview, current_question_id: 'q1' }} answerInterrupt={null} />)
    expect(screen.getByRole('button', { name: '上一题' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下一题' })).not.toBeDisabled()
  })
})