/** 通用审核容器：按 interrupt 类型分发已实现的表单，并统一展示错误。 */
import { useState } from 'react'

import { LowScoreReviewForm } from './LowScoreReviewForm'
import { InterviewAnswerForm } from './InterviewAnswerForm'
import { EvaluationUnavailableForm } from './EvaluationUnavailableForm'
import type { ThreadInterrupt, ThreadReviewCommand } from '../types'

type ReviewError = { code?: string; message: string } | null

/** 渲染最终审核中的 JD 草稿，字段仅来自后端冻结的 JDParsed 契约。 */
function JDReviewDraft({ draft }: { draft?: Record<string, unknown> }) {
  if (!draft) return null
  const list = (key: string) => Array.isArray(draft[key]) ? draft[key] as unknown[] : []
  const skills = list('skills')
  return <section className="jd-review-draft" aria-label="JD 解析草稿">
    <h3>JD 解析草稿</h3>
    <p><strong>职位：</strong>{String(draft.job_title ?? 'unknown')}</p>
    <p><strong>级别：</strong>{String(draft.seniority ?? 'unknown')}</p>
    <p><strong>公司：</strong>{draft.company_name ? String(draft.company_name) : '未提供'}</p>
    <p><strong>岗位职责：</strong>{list('responsibilities').map(String).join('；') || '未提供'}</p>
    <p><strong>核心技能：</strong>{skills.map((skill) => typeof skill === 'object' && skill !== null ? String((skill as Record<string, unknown>).name ?? '') : '').filter(Boolean).join('、') || '未提供'}</p>
    <p><strong>经验要求：</strong>{list('experience_requirements').map(String).join('；') || '未提供'}</p>
    <p><strong>学历要求：</strong>{list('education_requirements').map(String).join('；') || '未提供'}</p>
    {list('ambiguities').length > 0 ? <p><strong>待确认：</strong>{list('ambiguities').map(String).join('；')}</p> : null}
  </section>
}

export function ThreadReviewPanel({ interrupt, isResuming, error, onResume, onRetry, onRefresh }: {
  interrupt: ThreadInterrupt
  isResuming: boolean
  error: ReviewError
  onResume: (command: ThreadReviewCommand) => void
  onRetry?: () => void
  onRefresh?: () => void
}) {
  const [feedback, setFeedback] = useState('')
  const title = interrupt.type === 'final_review'
    ? interrupt.target === 'jd_parsed' ? '审核 JD 解析结果' : interrupt.target === 'match_result' ? '审核简历匹配结果' : '审核面试报告'
    : interrupt.type === 'low_match_score' ? '确认低匹配分结果' : '人工审核'
  return <section className="panel">
    <h2>{title}</h2>
    {interrupt.type === 'low_match_score' ? <LowScoreReviewForm interrupt={interrupt} disabled={isResuming} onSubmit={onResume} /> : interrupt.type === 'interview_answer' ? <InterviewAnswerForm interrupt={interrupt} disabled={isResuming} onSubmit={onResume} /> : interrupt.type === 'interview_evaluation_unavailable' ? <EvaluationUnavailableForm interrupt={interrupt} disabled={isResuming} onSubmit={onResume} /> : interrupt.type === 'final_review' ? <><>{interrupt.target === 'jd_parsed' ? <JDReviewDraft draft={interrupt.draft} /> : null}</><div className="actions-row">
      <button type="button" className="primary-action" disabled={isResuming} onClick={() => onResume({ action: 'approve' })}>{isResuming ? '提交中...' : '核可'}</button>
      <label className="field-label" htmlFor="final-review-feedback">驳回反馈</label>
      <textarea id="final-review-feedback" className="jd-input" value={feedback} onChange={(event) => setFeedback(event.target.value)} rows={3} disabled={isResuming} />
      <button type="button" disabled={isResuming || !feedback.trim()} onClick={() => onResume({ action: 'reject', feedback: feedback.trim() })}>驳回</button>
    </div></> : <p className="error-text">当前审核表单尚未实现。</p>}
    {error ? <div role="alert" className="error-text">
      {error.code === 'IDEMPOTENCY_KEY_REUSED' ? <><p>当前表单提交参数与已使用的幂等键不一致</p><button type="button" onClick={onRefresh}>刷新当前状态</button></> : null}
      {error.code === 'RESUME_IN_PROGRESS' ? <><p>同线程已有恢复请求正在处理，请稍后重试</p><button type="button" onClick={onRetry}>重试</button></> : null}
      {error.code === 'CHECKPOINT_NOT_FOUND' ? <><p>该审核已结束或线程不存在</p><button type="button" onClick={onRefresh}>刷新当前状态</button></> : null}
      {error.code?.startsWith('HITL_') || error.code === 'RESUME_REQUEST_INVALID' ? <p>{error.message}</p> : null}
      {!['IDEMPOTENCY_KEY_REUSED', 'RESUME_IN_PROGRESS', 'CHECKPOINT_NOT_FOUND', 'RESUME_REQUEST_INVALID'].includes(error.code ?? '') && !error.code?.startsWith('HITL_') ? <><p>发生错误，可重试</p><button type="button" onClick={onRetry}>重试</button></> : null}
    </div> : null}
  </section>
}