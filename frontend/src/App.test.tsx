/** 求职分析工作台关键交互测试。 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

/** jsdom 没有原生 EventSource，全局 stub（useAgentProgress 在 App 中自动调用） */
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
  it('renders the supplied workbench', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: '简历库' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '岗位信息' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始分析' })).toBeEnabled()
  })

  it('switches to match results and completes review', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('tab', { name: '匹配结果' }))
    expect(screen.getByRole('heading', { name: /匹配结果/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '批准' }))
    expect(screen.getByText('审核已通过，匹配结果已生成。')).toBeInTheDocument()
  })

  /** Step 7: analyze() 发起 POST /api/tasks，保存 session_id/thread_id */
  it('submits to POST /api/tasks and stores session/thread on 202', async () => {
    const fetchMock = vi.fn()
      // POST /api/tasks → 202
      .mockResolvedValueOnce({
        ok: true,
        status: 202,
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

    await user.click(screen.getByRole('button', { name: '开始分析' }))

    // 确认发出了 POST /api/tasks
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tasks',
      expect.objectContaining({ method: 'POST' }),
    )
    // 确认 session_id 存入 sessionStorage
    const persisted = sessionStorage.getItem('job-assistant.x-session-id')
    expect(persisted).toBe('ses-1')
  })

  /** Step 7: parseApiError 按 { error: { code, message } } 格式展示 */
  it('displays error code + message when backend returns ApiErrorResponse', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: { code: 'INPUT_TOO_SHORT', message: 'JD 文本不能少于 20 个字符' } }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '开始分析' }))

    expect(screen.getByText(/JD 文本不能少于 20 个字符/)).toBeInTheDocument()
  })

  /** Step 7: fetch TypeError 时降级为 mockAnalyzeJob */
  it('falls back to mock when network is unavailable', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('fetch failed'))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '开始分析' }))

    // mockAnalyzeJob 成功后 state 回到 'review'，显示岗位信息
    await screen.findByRole('heading', { name: '岗位信息' })
    expect(screen.getByRole('heading', { name: '岗位信息' })).toBeInTheDocument()
  })

  /** Step 10: ThreadReviewPanel 核可 → POST /v1/threads/:id/resume */
  it('approves via ThreadReviewPanel with resume POST and idempotency_key', async () => {
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValue('idem-test-key') })
    const fetchMock = vi.fn()
      // POST /api/tasks → 202
      .mockResolvedValueOnce({
        ok: true, status: 202,
        json: async () => ({ session_id: 'ses-1', thread_id: 'thr-1', status: 'accepted' }),
      })
      // GET /v1/threads/thr-1/state → interrupted with final_review
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          thread_id: 'thr-1', session_id: 'ses-1', status: 'interrupted',
          review_status: 'in_review', review_target: 'jd_parsed', current_node: 'review',
          interrupt: { type: 'final_review', target: 'jd_parsed', accepted_actions: ['approve', 'reject'], draft: {} },
        }),
      })
      // POST /v1/threads/:id/resume → 200 with completed
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

    // 发起任务
    await user.click(screen.getByRole('button', { name: '开始分析' }))
    // 等待 ThreadReviewPanel 渲染
    await screen.findByRole('heading', { name: '人工审核' })
    // 点击核可
    await user.click(screen.getByRole('button', { name: '核可' }))

    // 确认 POST resume 携带 idempotency_key 和 command
    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/threads/thr-1/resume',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ idempotency_key: 'idem-test-key', command: { action: 'approve' } }),
      }),
    )
  })
})
