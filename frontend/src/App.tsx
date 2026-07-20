import { useMemo, useState } from 'react'

import { useAgentProgress } from './hooks/useAgentProgress'
import { useThreadReview } from './hooks/useThreadReview'
import type { RunStatus, TaskAcceptedResponse } from './types'

function App() {
  const [jdText, setJdText] = useState(
    '后端工程师岗位，要求熟悉 Java、Spring Boot，并具备三年以上接口设计经验。',
  )
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [threadId, setThreadId] = useState<string | null>(null)
  const [requestError, setRequestError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const progress = useAgentProgress(sessionId)
  const review = useThreadReview(threadId, sessionId)

  const statusTone = useMemo<Record<RunStatus, string>>(
    () => ({
      idle: 'muted',
      running: 'running',
      interrupted: 'warning',
      resuming: 'running',
      completed: 'success',
      failed: 'danger',
    }),
    [],
  )

  async function handleStartRun() {
    setIsSubmitting(true)
    setRequestError(null)

    try {
      const response = await fetch('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ jd_text: jdText }),
      })

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { error?: { message?: string } }
          | null
        throw new Error(payload?.error?.message ?? '任务启动失败，请检查后端是否已运行。')
      }

      const payload = (await response.json()) as TaskAcceptedResponse
      setSessionId(payload.session_id)
      setThreadId(payload.thread_id)
    } catch (error) {
      setRequestError(
        error instanceof Error ? error.message : '任务启动失败，请检查前后端服务状态。',
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <main className="app-shell">
      <section className="composer">
        <div className="section-heading">
          <h1>Agent Progress Console</h1>
          <p>发起异步任务后，当前连接会实时展示 Graph 节点事件。</p>
        </div>

        <label className="field-label" htmlFor="jd-text">
          JD 文本
        </label>
        <textarea
          id="jd-text"
          className="jd-input"
          value={jdText}
          onChange={(event) => setJdText(event.target.value)}
          rows={8}
          placeholder="请输入岗位描述，随后点击启动任务。"
        />

        <div className="actions-row">
          <button
            type="button"
            className="primary-action"
            onClick={handleStartRun}
            disabled={isSubmitting || !jdText.trim()}
          >
            {isSubmitting ? '启动中...' : '启动异步任务'}
          </button>
          <span className={`status-pill ${statusTone[progress.status]}`}>
            {progress.status}
          </span>
        </div>

        {requestError ? <p className="error-text">{requestError}</p> : null}
        {progress.errorMessage ? <p className="error-text">{progress.errorMessage}</p> : null}
      </section>

      <section className="overview-grid">
        <div className="panel">
          <h2>运行概览</h2>
          <dl className="meta-list">
            <div>
              <dt>session_id</dt>
              <dd>{sessionId ?? '-'}</dd>
            </div>
            <div>
              <dt>thread_id</dt>
              <dd>{threadId ?? progress.threadId ?? '-'}</dd>
            </div>
            <div>
              <dt>当前节点</dt>
              <dd>{progress.currentNode ?? '-'}</dd>
            </div>
            <div>
              <dt>最后事件 ID</dt>
              <dd>{progress.lastEventId ?? '-'}</dd>
            </div>
            <div>
              <dt>SSE 连接</dt>
              <dd>{progress.isConnected ? 'connected' : 'idle'}</dd>
            </div>
          </dl>
        </div>

        <div className="panel">
          <h2>已完成节点</h2>
          <ul className="node-list">
            {progress.completedNodes.length === 0 ? (
              <li className="empty-state">尚无已完成节点</li>
            ) : (
              progress.completedNodes.map((node) => <li key={node}>{node}</li>)
            )}
          </ul>
        </div>
      </section>

      {review.state?.interrupt ? (
        <section className="panel">
          <h2>人工审核</h2>
          {review.state.interrupt.type === 'final_review' ? (
            <div className="actions-row">
              <button type="button" className="primary-action" disabled={review.isResuming} onClick={() => void review.resume({ action: 'approve' })}>
                {review.isResuming ? '提交中...' : '核可'}
              </button>
              <button type="button" disabled={review.isResuming} onClick={() => void review.resume({ action: 'reject', feedback: '请调整报告' })}>
                驳回
              </button>
            </div>
          ) : (
            <p className="error-text">当前 interrupt 类型将在 Task 11 提供专用表单。</p>
          )}
          {review.error ? <p className="error-text">{review.error}</p> : null}
        </section>
      ) : null}

      <section className="panel timeline-panel">
        <h2>事件轨迹</h2>
        <ul className="timeline-list">
          {progress.events.length === 0 ? (
            <li className="empty-state">启动任务后，SSE 事件会按到达顺序显示在这里。</li>
          ) : (
            progress.events.map((event) => (
              <li key={event.event_id} className="timeline-item">
                <div className="timeline-head">
                  <strong>{event.event}</strong>
                  <span>{event.timestamp}</span>
                </div>
                <div className="timeline-body">
                  <span>node: {event.node ?? '-'}</span>
                  <span>detail: {event.detail ?? event.input_summary ?? '-'}</span>
                </div>
              </li>
            ))
          )}
        </ul>
      </section>
    </main>
  )
}

export default App
