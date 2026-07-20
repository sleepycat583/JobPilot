/** App 入口组件的测试基础设施冒烟用例。 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { vi } from 'vitest'

import App from './App'

const resume = vi.fn()

vi.mock('./hooks/useThreadReview', () => ({
  useThreadReview: () => ({
    state: { interrupt: { type: 'final_review' } },
    isResuming: false,
    error: null,
    resume,
    loadState: vi.fn(),
  }),
}))

describe('App', () => {
  it('renders the task composer', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'Agent Progress Console' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '启动异步任务' })).toBeEnabled()
  })

  it('requires real feedback before rejecting a final review', async () => {
    const user = userEvent.setup()
    render(<App />)

    const reject = screen.getByRole('button', { name: '驳回' })
    expect(reject).toBeDisabled()
    await user.type(screen.getByLabelText('驳回反馈'), '请补充可验证的行动建议')
    await user.click(reject)

    expect(resume).toHaveBeenCalledWith({ action: 'reject', feedback: '请补充可验证的行动建议' })
  })
})