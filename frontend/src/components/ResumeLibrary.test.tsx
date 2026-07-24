/** 简历库组件行为测试：状态呈现、选择和失败版本重试。 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ResumeLibrary } from './ResumeLibrary'
import type { ResumeDto } from '../types'

const indexedResume: ResumeDto = {
  resume_id: '11111111-1111-4111-8111-111111111111', display_version: 3, file_name: '候选人-后端.txt', file_size: 1024,
  created_at: '2026-07-24T00:00:00Z', updated_at: '2026-07-24T00:00:00Z', index_status: 'indexed', error_code: null, error_message: null,
}

const failedResume: ResumeDto = {
  ...indexedResume, resume_id: '22222222-2222-4222-8222-222222222222', display_version: 4, file_name: '候选人-AI.txt', index_status: 'failed', error_code: 'INDEX_FAILED', error_message: '向量服务暂不可用',
}

function renderLibrary(overrides: Partial<React.ComponentProps<typeof ResumeLibrary>> = {}) {
  const props = {
    resumes: [indexedResume, failedResume], selectedResumeId: '', isLoading: false, isUploading: false, retryingResumeId: null, error: null,
    onSelect: vi.fn(), onClearSelection: vi.fn(), onUpload: vi.fn(), onRetry: vi.fn(), onRefresh: vi.fn(), ...overrides,
  }
  render(<ResumeLibrary {...props} />)
  return props
}

describe('ResumeLibrary', () => {
  it('renders frozen version fields and only enables indexed resumes', () => {
    renderLibrary()
    expect(screen.getByText('v3 · 候选人-后端.txt')).toBeInTheDocument()
    expect(screen.queryByText('正在建立索引', { exact: false })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /v3 · 候选人-后端/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /v4 · 候选人-AI/ })).toBeDisabled()
    expect(screen.getByText('向量服务暂不可用')).toBeInTheDocument()
  })

  it('selects an indexed version and requests retry for a failed version', async () => {
    const user = userEvent.setup()
    const props = renderLibrary()
    await user.click(screen.getByRole('button', { name: /v3 · 候选人-后端/ }))
    await user.click(screen.getByRole('button', { name: '重试索引' }))
    expect(props.onSelect).toHaveBeenCalledWith(indexedResume)
    expect(props.onRetry).toHaveBeenCalledWith(failedResume.resume_id)
  })

  it('allows the user to explicitly clear the selected resume', async () => {
    const user = userEvent.setup()
    const props = renderLibrary({ selectedResumeId: indexedResume.resume_id })
    await user.click(screen.getByRole('button', { name: '取消选择简历' }))
    expect(props.onClearSelection).toHaveBeenCalledTimes(1)
  })
})