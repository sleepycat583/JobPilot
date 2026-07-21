/** 求职分析工作台 API 对接测试。 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

/** jsdom 无原生 EventSource，全局 stub */
class EventSourceStub {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  url: string
  readyState = 1
  onerror: (() => void) | null = null
  constructor(url: string) { this.url = url }
  addEventListener() {}
  removeEventListener() {}
  close() { this.readyState = 2 }
}

beforeAll(() => {
  vi.stubGlobal('EventSource', EventSourceStub)
})

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('App', () => {
  it('renders the workbench with empty resume state', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: '简历库' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '职位描述' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始分析' })).toBeEnabled()
    expect(screen.getByText('暂无简历，请上传并完成索引。')).toBeInTheDocument()
  })

  it('submits to POST /api/tasks and stores session/thread on 202', async () => {
    const fetchMock = vi.fn()
      // POST /api/tasks → 202
      .mockResolvedValueOnce({
        ok: true, status: 202,
        json: async () => ({ session_id: 'ses-1', thread_id: 'thr-1', status: 'accepted' }),
      })
      // GET /v1/threads/thr-1/state → completed（useThreadReview 自动加载）
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ thread_id: 'thr-1', session_id: 'ses-1', status: 'completed', review_status: null, review_target: null, current_node: null, interrupt: null }),
      })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    // 填入 JD 文本
    await user.type(screen.getByRole('textbox', { name: '职位描述' }), '招聘 AI 工程师，要求熟悉 Python 和 LangGraph。')
    await user.click(screen.getByRole('button', { name: '开始分析' }))

    expect(fetchMock).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({ method: 'POST' }))
    const persisted = sessionStorage.getItem('job-assistant.x-session-id')
    expect(persisted).toBe('ses-1')
  })

  it('displays error code + message when backend returns ApiErrorResponse', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status: 400,
      json: async () => ({ error: { code: 'INPUT_TOO_SHORT', message: 'JD 文本不能少于 20 个字符' } }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.type(screen.getByRole('textbox', { name: '职位描述' }), 'short')
    await user.click(screen.getByRole('button', { name: '开始分析' }))

    expect(screen.getByText(/JD 文本不能少于 20 个字符/)).toBeInTheDocument()
  })

  it('approves via ThreadReviewPanel with resume POST and idempotency_key', async () => {
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValue('idem-test-key') })
    const fetchMock = vi.fn()
      // POST /api/tasks
      .mockResolvedValueOnce({
        ok: true, status: 202,
        json: async () => ({ session_id: 'ses-1', thread_id: 'thr-1', status: 'accepted' }),
      })
      // GET /v1/threads/thr-1/state → interrupted
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          thread_id: 'thr-1', session_id: 'ses-1', status: 'interrupted',
          review_status: 'in_review', review_target: 'jd_parsed', current_node: 'review',
          interrupt: { type: 'final_review', target: 'jd_parsed', accepted_actions: ['approve', 'reject'], draft: {} },
        }),
      })
      // POST /v1/threads/:id/resume → 200
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          thread_id: 'thr-1', session_id: 'ses-1', status: 'completed',
          review_status: 'approved', review_target: 'jd_parsed', current_node: null, interrupt: null,
        }),
      })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.type(screen.getByRole('textbox', { name: '职位描述' }), '招聘 AI 工程师，要求熟悉 Python 和 LangGraph。')
    await user.click(screen.getByRole('button', { name: '开始分析' }))
    await screen.findByRole('heading', { name: '人工审核' })
    await user.click(screen.getByRole('button', { name: '核可' }))

    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/threads/thr-1/resume',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ idempotency_key: 'idem-test-key', command: { action: 'approve' } }),
      }),
    )
  })
})
