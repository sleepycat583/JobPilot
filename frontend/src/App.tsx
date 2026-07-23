/** 求职分析工作台入口，通过 /api/tasks + SSE + /v1/threads 对接后端。 */
import { useEffect, useState } from 'react'

import { JD_PROGRESS_NODES, useAgentProgress } from './hooks/useAgentProgress'
import { useThreadReview } from './hooks/useThreadReview'
import { ThreadReviewPanel } from './components/ThreadReviewPanel'
import { JDResultPanel } from './components/JDResultPanel'
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
  const progress = useAgentProgress(sessionId, threadId)

  // 线程审核状态 — 从 SSE 同步 threadId 或持久化 thread 加载
  const review = useThreadReview(threadId ?? progress.threadId, sessionId ?? progress.sessionId)
  const currentReviewState = review.state?.thread_id === threadId ? review.state : null

  /**
   * 综合 SSE 和 REST API 两条通路推导统一状态。
   *
   * 为什么这样做：
   *   SSE 事件可能因网络抖动或事件总线内部静默吞异常而丢失，
   *   REST API 轮询拿到的 interrupt/completed 状态作为兜底，
   *   确保"任务已受理"提示和按钮 disabled 不会永远卡在 running。
   */
  const effectiveStatus = (() => {
    if (progress.status === 'idle') return 'idle'
    if (currentReviewState?.status === 'failed' || progress.status === 'failed') return 'failed'
    if (currentReviewState?.status === 'completed' || progress.status === 'completed') return 'completed'
    if (currentReviewState?.interrupt || progress.status === 'interrupted') return 'interrupted'
    if (progress.status === 'running' && Boolean(progress.errorMessage)) return 'failed'
    return progress.status
  })()

  useEffect(() => {
    if (progress.status !== 'interrupted' || !progress.threadId || review.state?.interrupt) return
    // interrupt_required 可能晚于首次 state 查询落盘；收到事件后再次读取，确保表单以 Checkpoint 为准。
    void review.loadState(progress.threadId).catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : '无法读取审核状态。')
    })
  }, [progress.status, progress.threadId, review.loadState, currentReviewState?.interrupt])

  useEffect(() => {
    const jdParserFinished = progress.nodeProgress.jd_parser === 'completed'
    const taskReachedTerminalState = progress.status === 'completed'
    const hasResultOrInterrupt = Boolean(currentReviewState?.jd_parsed || currentReviewState?.interrupt)
    if (!threadId || hasResultOrInterrupt || (!jdParserFinished && !taskReachedTerminalState)) return

    let cancelled = false
    // 节点结束事件可能比 Checkpoint 可读早一个调度周期；有限重试只等待已完成的持久化写入。
    const loadParsedResult = async () => {
      for (let attempt = 0; attempt < 4 && !cancelled; attempt += 1) {
        try {
          const state = await review.loadState(threadId)
          // completed 只代表 Graph 结束；结果字段可能随后才写入 Checkpoint，不能提前停止重试。
          if (state.jd_parsed || state.interrupt) return
        } catch {
          // 线程刚创建时 Checkpoint 尚不存在，等待下一次短重试。
        }
        await new Promise((resolve) => window.setTimeout(resolve, 150))
      }
    }
    void loadParsedResult()
    return () => { cancelled = true }
  }, [progress.nodeProgress.jd_parser, progress.status, review.loadState, currentReviewState?.jd_parsed, currentReviewState?.interrupt, threadId])

  useEffect(() => {
    if (!threadId || progress.status !== 'running' || currentReviewState?.interrupt) return

    // 异步任务刚受理时，首次状态查询可能早于 Graph 写入 interrupt；SSE 若因
    // 开发代理重连等原因未送达 interrupt_required，轮询 Checkpoint 仍可恢复审核表单。
    const timer = window.setInterval(() => {
      void review.loadState(threadId).catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : '无法读取审核状态。')
      })
    }, 800)
    return () => window.clearInterval(timer)
  }, [progress.status, review.loadState, currentReviewState?.interrupt, threadId])

  useEffect(() => {
    if (!currentReviewState) return
    // Checkpoint 是 SSE 丢失或恢复后端执行时的权威兜底，只同步整体状态和当前节点。
    progress.syncCheckpointState(
      currentReviewState.status === 'interrupted' ? 'interrupted' : currentReviewState.status,
      currentReviewState.current_node,
    )
  }, [currentReviewState, progress.syncCheckpointState])

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
      <div className="disabled-tools">
        <div>模拟面试<span>功能开发中</span></div>
        <div>历史记录<span>功能开发中</span></div>
      </div>
    </aside>

    <section className="analysis-area">
      <label htmlFor="jd-input" className="input-label">粘贴职位描述（JD）</label>
      <textarea id="jd-input" className="jd-input" value={jdText} onChange={(e) => setJdText(e.target.value)} aria-label="职位描述" />
      <div className="analysis-action-row">
        <p>将匹配: <b>{fileName}</b><span>（不选则仅做 JD 解析）</span></p>
        <button type="button" className="primary-button" onClick={() => void analyze()} disabled={effectiveStatus === 'running'}>{effectiveStatus === 'running' ? '分析中...' : '开始分析'}</button>
      </div>
      <div className="tabs" role="tablist" aria-label="分析结果">
        <button type="button" role="tab" aria-selected={tab === 'jd'} className={tab === 'jd' ? 'active' : ''} onClick={() => setTab('jd')}>JD 解析</button>
        <button type="button" role="tab" aria-selected={tab === 'match'} className={tab === 'match' ? 'active' : ''} onClick={() => setTab('match')}>匹配结果</button>
      </div>
      {effectiveStatus === 'running' && <div className="state-card loading-state"><span className="spinner" />任务已受理，等待后端执行...</div>}
      {error && <div className="state-card error-state"><p>{error}</p><button type="button" className="secondary-button" onClick={() => void analyze()}>重试</button></div>}
      {effectiveStatus === 'failed' && !error ? <div className="state-card error-state"><p>{progress.errorMessage ?? '任务执行失败，请检查模型服务连接后重试。'}</p><button type="button" className="secondary-button" onClick={() => void analyze()}>重试</button></div> : null}
      {currentReviewState?.jd_parsed ? <JDResultPanel result={currentReviewState.jd_parsed} /> : null}
      {currentReviewState?.interrupt ? (
        <ThreadReviewPanel
          interrupt={currentReviewState.interrupt}
          isResuming={review.isResuming}
          error={review.error}
          onResume={(command: ThreadReviewCommand) => { void review.resume(command) }}
          onRetry={review.retry}
          onRefresh={() => { void review.loadState(currentReviewState.thread_id) }}
        />
      ) : effectiveStatus === 'completed' && currentReviewState?.jd_parsed ? (
        <p className="completion-note">任务已完成。</p>
      ) : effectiveStatus === 'completed' ? (
        <div className="state-card loading-state"><span className="spinner" />任务已完成，正在同步解析结果...</div>
      ) : null}
      {threadId && <footer className="thread-footer">thread_id: {threadId}</footer>}
    </section>

    <aside className="progress-sidebar" aria-label="执行进度">
      <h2>执行进度</h2>
      {effectiveStatus === 'idle' ? (
        <p className="empty-hint">提交 JD 后在此查看实时进度。</p>
      ) : (
        <ol className="progress-list">
          {JD_PROGRESS_NODES.map((node) => {
            const nodeStatus = progress.nodeProgress[node]
            return <li key={node} className={nodeStatus === 'completed' ? 'done' : nodeStatus === 'failed' ? 'error' : nodeStatus === 'interrupted' || nodeStatus === 'running' ? 'current' : 'pending'}>
              <span>{nodeStatus === 'completed' ? '✓' : nodeStatus === 'failed' ? '✗' : ''}</span>{progressLabel(node)}
            </li>
          })}
          {effectiveStatus === 'failed' && progress.errorMessage && (
            <li className="error"><span>✗</span>{progress.errorMessage}</li>
          )}
        </ol>
      )}

    </aside>
  </main>
}

function progressLabel(node: string): string {
  const labels: Record<string, string> = {
    rolling_summary: '准备上下文', supervisor: '识别任务', queue_dispatch: '分发任务',
    jd_parser: '解析职位描述', prepare_final_review: '准备审核',
    final_review_gate: '等待人工审核', finalize_node: '生成最终结果', api: '完成任务',
  }
  return labels[node] ?? node
}

export default App
