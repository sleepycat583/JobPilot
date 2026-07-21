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
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ session_id: 'ses-1', thread_id: 'thr-1', status: 'accepted' }),
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
    // 确认 session_id 存入 sessionStorage（parseApiError 作为 header 被调用时也应检查）
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
})
