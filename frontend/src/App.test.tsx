/** 求职分析工作台 API 对接测试。 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => body }
}

function emptyResumeLibraryResponse() {
  return jsonResponse({ resumes: [] })
}

const indexedResume = {
  resume_id: '11111111-1111-4111-8111-111111111111', display_version: 3, file_name: '候选人-后端.txt', file_size: 1024,
  created_at: '2026-07-24T00:00:00Z', updated_at: '2026-07-24T00:00:00Z', index_status: 'indexed', error_code: null, error_message: null,
} as const

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
  it('renders the workbench with empty resume state', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(emptyResumeLibraryResponse())))
    render(<App />)
    expect(screen.getByRole('heading', { name: '简历库' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '职位描述' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '解析 JD' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '开始匹配' })).toBeDisabled()
    expect(await screen.findByText('暂无简历，请上传并完成索引。')).toBeInTheDocument()
  })

  it('submits to POST /api/tasks and stores session/thread on 202', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === '/v1/resumes') return Promise.resolve(emptyResumeLibraryResponse())
      if (url === '/api/tasks') return Promise.resolve(jsonResponse({ session_id: 'ses-1', thread_id: 'thr-1', status: 'accepted' }, true, 202))
      if (url === '/v1/threads/thr-1/state') return Promise.resolve(jsonResponse({ thread_id: 'thr-1', session_id: 'ses-1', status: 'completed', review_status: null, review_target: null, current_node: null, interrupt: null }))
      return Promise.reject(new Error(`unexpected request: ${url}`))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    // 填入 JD 文本
    await user.type(screen.getByRole('textbox', { name: '职位描述' }), '招聘 AI 工程师，要求熟悉 Python 和 LangGraph。')
    await user.click(screen.getByRole('button', { name: '解析 JD' }))

    expect(fetchMock).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({ method: 'POST' }))
    const persisted = sessionStorage.getItem('job-assistant.x-session-id')
    expect(persisted).toBe('ses-1')
  })

  it('displays error code + message when backend returns ApiErrorResponse', async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(url === '/v1/resumes'
      ? emptyResumeLibraryResponse()
      : jsonResponse({ error: { code: 'INPUT_TOO_SHORT', message: 'JD 文本不能少于 20 个字符' } }, false, 400)))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.type(screen.getByRole('textbox', { name: '职位描述' }), 'short')
    await user.click(screen.getByRole('button', { name: '解析 JD' }))

    expect(screen.getAllByText(/JD 文本不能少于 20 个字符/)).not.toHaveLength(0)
  })

  it('does not submit resume_id when parsing JD even if a resume is selected', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === '/v1/resumes') return Promise.resolve(jsonResponse({ resumes: [indexedResume] }))
      if (url === '/api/tasks') return Promise.resolve(jsonResponse({ session_id: 'ses-1', thread_id: 'thr-1', status: 'accepted' }, true, 202))
      if (url === '/v1/threads/thr-1/state') return Promise.resolve(jsonResponse({ thread_id: 'thr-1', session_id: 'ses-1', status: 'completed', review_status: null, review_target: null, current_node: null, interrupt: null }))
      return Promise.reject(new Error(`unexpected request: ${url}`))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: /v3 · 候选人-后端/ }))
    await user.type(screen.getByRole('textbox', { name: '职位描述' }), '招聘 AI 工程师，要求熟悉 Python 和 LangGraph。')
    await user.click(screen.getByRole('button', { name: '解析 JD' }))

    expect(fetchMock).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({
      body: JSON.stringify({ jd_text: '招聘 AI 工程师，要求熟悉 Python 和 LangGraph。' }),
    }))
  })

  it('submits the selected resume and renders the returned match result', async () => {
    const matchResult = {
      total_score: 82, dimension_scores: { must: 85 }, matched_items: [], strengths: ['Java 基础扎实'], gaps: [],
      recommendations: ['补充架构案例'], low_score_review_required: false, resume_id: indexedResume.resume_id,
    }
    const fetchMock = vi.fn((url: string) => {
      if (url === '/v1/resumes') return Promise.resolve(jsonResponse({ resumes: [indexedResume] }))
      if (url === '/api/tasks') return Promise.resolve(jsonResponse({ session_id: 'ses-match', thread_id: 'thr-match', status: 'accepted' }, true, 202))
      if (url === '/v1/threads/thr-match/state') return Promise.resolve(jsonResponse({
        thread_id: 'thr-match', session_id: 'ses-match', status: 'interrupted', review_status: 'in_review',
        review_target: 'match_result', current_node: 'prepare_final_review', interrupt: { type: 'final_review', target: 'match_result', accepted_actions: ['approve', 'reject'], draft: matchResult }, match_result: matchResult,
      }))
      return Promise.reject(new Error(`unexpected request: ${url}`))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: /v3 · 候选人-后端/ }))
    await user.type(screen.getByRole('textbox', { name: '职位描述' }), '招聘后端工程师，要求熟悉 Java、Spring Boot 和接口设计。')
    await user.click(screen.getByRole('button', { name: '开始匹配' }))

    expect(fetchMock).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({
      body: JSON.stringify({ jd_text: '招聘后端工程师，要求熟悉 Java、Spring Boot 和接口设计。', resume_id: indexedResume.resume_id }),
    }))
    expect(await screen.findByRole('heading', { name: '简历匹配结果 82 分' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '审核简历匹配结果' })).toBeInTheDocument()
  })

  it('approves via ThreadReviewPanel with resume POST and idempotency_key', async () => {
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValue('idem-test-key') })
    const fetchMock = vi.fn((url: string) => {
      if (url === '/v1/resumes') return Promise.resolve(emptyResumeLibraryResponse())
      if (url === '/api/tasks') return Promise.resolve(jsonResponse({ session_id: 'ses-1', thread_id: 'thr-1', status: 'accepted' }, true, 202))
      if (url === '/v1/threads/thr-1/state') return Promise.resolve(jsonResponse({
          thread_id: 'thr-1', session_id: 'ses-1', status: 'interrupted',
          review_status: 'in_review', review_target: 'jd_parsed', current_node: 'review',
          interrupt: { type: 'final_review', target: 'jd_parsed', accepted_actions: ['approve', 'reject'], draft: {} },
        }))
      if (url === '/v1/threads/thr-1/resume') return Promise.resolve(jsonResponse({
          thread_id: 'thr-1', session_id: 'ses-1', status: 'completed',
          review_status: 'approved', review_target: 'jd_parsed', current_node: null, interrupt: null,
        }))
      return Promise.reject(new Error(`unexpected request: ${url}`))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.type(screen.getByRole('textbox', { name: '职位描述' }), '招聘 AI 工程师，要求熟悉 Python 和 LangGraph。')
    await user.click(screen.getByRole('button', { name: '解析 JD' }))
    await screen.findByRole('heading', { name: '审核 JD 解析结果' })
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
