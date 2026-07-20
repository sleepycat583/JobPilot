/** 通用审核容器：按 interrupt 类型分发已实现的表单，并统一展示错误。 */
import { useState } from 'react'

import type { FinalReviewCommand, ThreadInterrupt } from '../types'

type ReviewError = { code?: string; message: string } | null

export function ThreadReviewPanel({ interrupt, isResuming, error, onResume }: {
  interrupt: ThreadInterrupt
  isResuming: boolean
  error: ReviewError
  onResume: (command: FinalReviewCommand) => void
}) {
  const [feedback, setFeedback] = useState('')
  return <section className="panel">
    <h2>人工审核</h2>
    {interrupt.type === 'final_review' ? <div className="actions-row">
      <button type="button" className="primary-action" disabled={isResuming} onClick={() => onResume({ action: 'approve' })}>{isResuming ? '提交中...' : '核可'}</button>
      <label className="field-label" htmlFor="final-review-feedback">驳回反馈</label>
      <textarea id="final-review-feedback" className="jd-input" value={feedback} onChange={(event) => setFeedback(event.target.value)} rows={3} disabled={isResuming} />
      <button type="button" disabled={isResuming || !feedback.trim()} onClick={() => onResume({ action: 'reject', feedback: feedback.trim() })}>驳回</button>
    </div> : <p className="error-text">当前审核表单尚未实现。</p>}
    {error ? <p className="error-text" role="alert">{error.code ? `${error.code}: ${error.message}` : error.message}</p> : null}
  </section>
}