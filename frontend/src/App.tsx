/** 求职分析工作台入口，通过 /api/tasks + SSE + /v1/threads 对接后端。 */
import { useEffect, useState } from 'react'

import { JD_PROGRESS_NODES, useAgentProgress } from './hooks/useAgentProgress'
import { useThreadReview } from './hooks/useThreadReview'
import { useResumeLibrary } from './hooks/useResumeLibrary'
import { ThreadReviewPanel } from './components/ThreadReviewPanel'
import { JDResultPanel } from './components/JDResultPanel'
import { MatchResultPanel } from './components/MatchResultPanel'
import { InterviewPanel } from './components/InterviewPanel'
import { ResumeLibrary } from './components/ResumeLibrary'
import type { ApiErrorResponse, InterviewTaskQueue, TaskAcceptedResponse, ThreadReviewCommand } from './types'

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
  const [tab, setTab] = useState<'jd' | 'match' | 'interview'>('jd')
  const [interviewMode, setInterviewMode] = useState<'jd' | 'match'>('jd')
  const [error, setError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(loadSessionId())
  const [threadId, setThreadId] = useState<string | null>(null)
  const resumeLibrary = useResumeLibrary()
  const selectedResume = resumeLibrary.resumes.find((resume) => resume.resume_id === resumeLibrary.selectedResumeId)
  const fileName = selectedResume?.file_name ?? '未选择'

  // SSE 进度 — sessionId 存在时订阅事件流
  const progress = useAgentProgress(sessionId, threadId)

  // 线程审核状态 — 从 SSE 同步 threadId 或持久化 thread 加载
  const review = useThreadReview(threadId ?? progress.threadId, sessionId ?? progress.sessionId)
  // 刷新页面时 threadId 尚未写回 App state；此时允许 useThreadReview 从 sessionStorage
  // 恢复的 Checkpoint 成为当前状态。新建线程后仍严格排除旧线程响应。
  const currentReviewState = review.state && (!threadId || review.state.thread_id === threadId) ? review.state : null
  const activeThreadId = threadId ?? currentReviewState?.thread_id ?? null

  /**
   * 综合 SSE 和 REST API 两条通路推导统一状态。
   *
   * 为什么这样做：
   *   SSE 事件可能因网络抖动或事件总线内部静默吞异常而丢失，
   *   REST API 轮询拿到的 interrupt/completed 状态作为兜底，
   *   确保"任务已受理"提示和按钮 disabled 不会永远卡在 running。
   */
  const effectiveStatus = (() => {
    if (currentReviewState?.status === 'failed' || progress.status === 'failed') return 'failed'
    if (currentReviewState?.status === 'completed' || progress.status === 'completed') return 'completed'
    if (currentReviewState?.interrupt || progress.status === 'interrupted') return 'interrupted'
    if (progress.status === 'running' && Boolean(progress.errorMessage)) return 'failed'
    if (currentReviewState?.status === 'running' || progress.status === 'running') return 'running'
    if (progress.status === 'idle') return 'idle'
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
    if (!activeThreadId || currentReviewState?.interrupt || currentReviewState?.status === 'completed' || currentReviewState?.status === 'failed') return

    // 异步任务刚受理时，首次状态查询可能早于 Graph 写入 interrupt；SSE 若因
    // 开发代理重连等原因未送达 interrupt_required，轮询 Checkpoint 仍可恢复审核表单。
    // 不以 SSE 的 running 状态作为前提：中断事件可能先于可读取的 Checkpoint 到达。
    const timer = window.setInterval(() => {
      void review.loadState(activeThreadId).catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : '无法读取审核状态。')
      })
    }, 800)
    return () => window.clearInterval(timer)
  }, [activeThreadId, review.loadState, currentReviewState?.interrupt, currentReviewState?.status])

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
  async function analyze(mode: 'jd' | 'match' | 'interview') {
    setError(null)
    try {
      const xSessionId = loadSessionId()
      const body: Record<string, unknown> = { jd_text: jdText }
      // 业务规则：JD 解析只依赖粘贴文本；只有用户主动点击匹配时才发送简历标识。
      if (mode === 'match' || (mode === 'interview' && interviewMode === 'match')) {
        if (!resumeLibrary.selectedResumeId) {
          setError('请先选择一份已建立索引的简历，再开始匹配。')
          return
        }
        body.resume_id = resumeLibrary.selectedResumeId
      }
      if (mode === 'interview') {
        body.task_queue = (interviewMode === 'match'
          ? ['jd_parse', 'resume_match', 'mock_interview']
          : ['jd_parse', 'mock_interview']) satisfies InterviewTaskQueue
      }
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
      setTab(mode)
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : '分析失败，请重试。')
    }
  }

  return <main className="workbench">
    <ResumeLibrary {...resumeLibrary} onSelect={resumeLibrary.select} onClearSelection={resumeLibrary.clearSelection} onUpload={(file) => { void resumeLibrary.upload(file) }} onRetry={(resumeId) => { void resumeLibrary.retry(resumeId) }} onRefresh={() => { void resumeLibrary.refresh() }} onStartInterview={() => setTab('interview')} />

    <section className="analysis-area">
      <label htmlFor="jd-input" className="input-label">粘贴职位描述（JD）</label>
      <textarea id="jd-input" className="jd-input" value={jdText} onChange={(e) => setJdText(e.target.value)} aria-label="职位描述" />
      <div className="analysis-action-row">
        <p>当前简历: <b>{fileName}</b><span>（JD 解析不会使用简历）</span></p>
        <div className="analysis-actions">
          <button type="button" className="secondary-button" onClick={() => void analyze('jd')} disabled={effectiveStatus === 'running'}>{effectiveStatus === 'running' ? '分析中...' : '解析 JD'}</button>
          <button type="button" className="primary-button" onClick={() => void analyze('match')} disabled={effectiveStatus === 'running' || !resumeLibrary.selectedResumeId}>开始匹配</button>
        </div>
      </div>
      <div className="tabs" role="tablist" aria-label="分析结果">
        <button type="button" role="tab" aria-selected={tab === 'jd'} className={tab === 'jd' ? 'active' : ''} onClick={() => setTab('jd')}>JD 解析</button>
        <button type="button" role="tab" aria-selected={tab === 'match'} className={tab === 'match' ? 'active' : ''} onClick={() => setTab('match')}>匹配结果</button>
        <button type="button" role="tab" aria-selected={tab === 'interview'} className={tab === 'interview' ? 'active' : ''} onClick={() => setTab('interview')}>模拟面试</button>
      </div>
      {effectiveStatus === 'running' && <div className="state-card loading-state"><span className="spinner" />任务已受理，等待后端执行...</div>}
      {error && <div className="state-card error-state"><p>{error}</p><button type="button" className="secondary-button" onClick={() => void analyze(tab)}>重试</button></div>}
      {effectiveStatus === 'failed' && !error ? <div className="state-card error-state"><p>{progress.errorMessage ?? '任务执行失败，请检查模型服务连接后重试。'}</p><button type="button" className="secondary-button" onClick={() => void analyze(tab)}>重试</button></div> : null}
      {tab === 'jd' && currentReviewState?.jd_parsed ? <JDResultPanel result={currentReviewState.jd_parsed} /> : null}
      {tab === 'match' && currentReviewState?.match_result ? <MatchResultPanel result={currentReviewState.match_result} /> : null}
      {tab === 'match' && !currentReviewState?.match_result && <div className="state-card empty-state"><p>选择已索引的简历后，点击“开始匹配”即可提交 JD 与简历的组合分析。</p></div>}
      {tab === 'interview' && !currentReviewState?.interview_state ? <section className="interview-start state-card">
        <h2>模拟面试</h2>
        <div className="segmented-control" role="group" aria-label="面试流程">
          <button type="button" className={interviewMode === 'jd' ? 'active' : ''} onClick={() => setInterviewMode('jd')}>按 JD 面试</button>
          <button type="button" className={interviewMode === 'match' ? 'active' : ''} onClick={() => setInterviewMode('match')}>匹配后面试</button>
        </div>
        <p>{interviewMode === 'jd' ? '先核可 JD 解析结果，再开始岗位相关的逐题模拟面试。' : '先核可 JD 与简历匹配结果，再根据匹配差距进行面试。'}</p>
        <button type="button" className="primary-button" onClick={() => void analyze('interview')} disabled={effectiveStatus === 'running' || (interviewMode === 'match' && !resumeLibrary.selectedResumeId)}>{effectiveStatus === 'running' ? '任务执行中...' : '开始面试'}</button>
        {interviewMode === 'match' && !resumeLibrary.selectedResumeId ? <small>请选择一份已建立索引的简历后再开始。</small> : null}
      </section> : null}
      {tab === 'interview' && currentReviewState?.interview_state ? <InterviewPanel interview={currentReviewState.interview_state} /> : null}
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
      {activeThreadId && <footer className="thread-footer">thread_id: {activeThreadId}</footer>}
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
    final_review_gate: '等待人工审核', resume_matcher: '匹配简历与岗位',
    prepare_low_score_review: '准备低分审核', low_score_gate: '等待低分确认',
    interview_plan: '生成面试计划', ask_question: '生成面试问题',
    interview_await_answer: '等待回答', evaluate_answer: '评估回答',
    interview_decision: '决定下一题', generate_review_report: '生成面试复盘',
    finalize_node: '生成最终结果', api: '完成任务',
  }
  return labels[node] ?? node
}

export default App
