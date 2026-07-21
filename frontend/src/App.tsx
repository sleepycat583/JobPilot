/** 求职分析工作台入口，通过 /api/tasks + SSE + /v1/threads 对接后端。 */
import { useState } from 'react'

import { useAgentProgress } from './hooks/useAgentProgress'
import { useThreadReview } from './hooks/useThreadReview'
import { ThreadReviewPanel } from './components/ThreadReviewPanel'
import type { ApiErrorResponse, TaskAcceptedResponse, ThreadReviewCommand } from './types'

// TODO(第 2 章): 对接简历库接口，替换空数组
const EMPTY_RESUMES: { resume_version: string; file_name: string; index_status: string }[] = []

/** X-Session-ID 持久化 key（第 0 章全局约束） */
const SESSION_STORAGE_KEY = 'job-assistant.x-session-id'

function loadSessionId(): string | null {
  return sessionStorage.getItem(SESSION_STORAGE_KEY)
}

function saveSessionId(id: string) {
  sessionStorage.setItem(SESSION_STORAGE_KEY, id)
}

/**
 * 解析后端统一错误响应 { error: { code, message } }（第 0 章）。
 */
function parseApiError(response: Response, body: unknown, fallback: string): Error & { code?: string } {
  const apiError = (body as ApiErrorResponse | null)?.error
  const message = apiError?.message ?? `${fallback} (HTTP ${response.status})`
  return Object.assign(new Error(message), { code: apiError?.code })
}

function App() {
  const [jdText, setJdText] = useState('')
  const [resumeVersion, setResumeVersion] = useState('')
  const [tab, setTab] = useState<'jd' | 'match'>('jd')
  const [error, setError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(loadSessionId())
  const [threadId, setThreadId] = useState<string | null>(null)
  const fileName = EMPTY_RESUMES.find((r) => r.resume_version === resumeVersion)?.file_name ?? '未选择'

  // SSE 进度 — sessionId 存在时订阅事件流
  const progress = useAgentProgress(sessionId)

  // 线程审核状态 — 从 SSE 同步 threadId 或持久化 thread 加载
  const review = useThreadReview(threadId ?? progress.threadId, sessionId ?? progress.sessionId)

  /**
   * POST /api/tasks 启动异步分析，保存 202 返回的 session_id/thread_id。
   *
   * 业务规则（第 0 章）：
   * - X-Session-ID 首次省略 → 后端生成 → 持久化 sessionStorage
   * - 后续请求复用持久化的 session_id
   * - 错误按 { error: { code, message } } 解析
   */
  async function analyze() {
    setError(null)
    try {
      const xSessionId = loadSessionId()
      const body: Record<string, unknown> = { jd_text: jdText }
      if (resumeVersion) body.resume_version = resumeVersion
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (xSessionId) headers['X-Session-ID'] = xSessionId

      const response = await fetch('/api/tasks', { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) {
        throw parseApiError(response, await response.json().catch(() => null), '任务提交失败')
      }
      const data = (await response.json()) as TaskAcceptedResponse
      saveSessionId(data.session_id)
      setSessionId(data.session_id)
      setThreadId(data.thread_id)
      setTab('jd')
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : '分析失败，请重试。')
    }
  }

  const hasResume = EMPTY_RESUMES.length > 0

  return <main className="workbench">
    <aside className="resume-sidebar" aria-label="简历库">
      <h1>简历库</h1>
      {hasResume ? (
        <div className="resume-list">
          {EMPTY_RESUMES.map((resume) => (
            <button key={resume.resume_version} type="button" className={`resume-item ${resumeVersion === resume.resume_version ? 'selected' : ''}`} onClick={() => setResumeVersion(resume.resume_version)}>
              <strong>{resume.file_name}</strong>
              <span className={`resume-status ${resume.index_status}`}><i />{resume.index_status === 'indexed' ? `已索引 · ${resume.resume_version}` : '索引失败'}</span>
            </button>
          ))}
        </div>
      ) : (
        <p className="empty-hint">暂无简历，请上传并完成索引。</p>
      )}
      <button type="button" className="upload-button" disabled>
        <span>＋</span>上传新版本简历
      </button>
    </aside>

    <section className="analysis-area">
      <label htmlFor="jd-input" className="input-label">粘贴职位描述（JD）</label>
      <textarea id="jd-input" className="jd-input" value={jdText} onChange={(e) => setJdText(e.target.value)} aria-label="职位描述" />
      <div className="analysis-action-row">
        <p>将匹配: <b>{fileName}</b><span>（不选则仅做 JD 解析）</span></p>
        <button type="button" className="primary-button" onClick={() => void analyze()} disabled={progress.status === 'running'}>{progress.status === 'running' ? '分析中...' : '开始分析'}</button>
      </div>
      <div className="tabs" role="tablist" aria-label="分析结果">
        <button type="button" role="tab" aria-selected={tab === 'jd'} className={tab === 'jd' ? 'active' : ''} onClick={() => setTab('jd')}>JD 解析</button>
        <button type="button" role="tab" aria-selected={tab === 'match'} className={tab === 'match' ? 'active' : ''} onClick={() => setTab('match')}>匹配结果</button>
      </div>
      {progress.status === 'running' && <div className="state-card loading-state"><span className="spinner" />任务已受理，等待后端执行...</div>}
      {error && <div className="state-card error-state"><p>{error}</p><button type="button" className="secondary-button" onClick={() => void analyze()}>重试</button></div>}
    </section>

    <aside className="progress-sidebar" aria-label="执行进度">
      <h2>执行进度</h2>
      {progress.status === 'idle' ? (
        <p className="empty-hint">提交 JD 后在此查看实时进度。</p>
      ) : (
        <ol className="progress-list">
          {progress.completedNodes.map((node) => (
            <li key={node} className="done"><span>✓</span>{node}</li>
          ))}
          {progress.currentNode && <li className="current"><span />{progress.currentNode}</li>}
          {progress.status === 'failed' && progress.errorMessage && (
            <li className="error"><span>✗</span>{progress.errorMessage}</li>
          )}
        </ol>
      )}

      {review.state?.interrupt ? (
        <ThreadReviewPanel
          interrupt={review.state.interrupt}
          isResuming={review.isResuming}
          error={review.error}
          onResume={(command: ThreadReviewCommand) => { void review.resume(command) }}
          onRetry={review.retry}
          onRefresh={() => { void review.loadState(review.state!.thread_id) }}
        />
      ) : progress.status === 'completed' ? (
        <p className="completion-note">任务已完成。</p>
      ) : null}

      <div className="disabled-tools">
        <div>模拟面试<span>功能开发中</span></div>
        <div>历史记录<span>功能开发中</span></div>
      </div>
      {threadId && <footer>thread_id: {threadId}</footer>}
    </aside>
  </main>
}

export default App
