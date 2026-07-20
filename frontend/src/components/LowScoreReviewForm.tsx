/** 低分匹配审核表单：展示风险上下文并提交继续、取消或修订输入命令。 */
import { useState } from 'react'
import type { LowScoreReviewCommand, ThreadInterrupt } from '../types'

export function LowScoreReviewForm({ interrupt, disabled, onSubmit }: { interrupt: Extract<ThreadInterrupt, { type: 'low_match_score' }>; disabled: boolean; onSubmit: (command: LowScoreReviewCommand) => void }) {
  const [revising, setRevising] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [resumeVersion, setResumeVersion] = useState('')
  const [jdText, setJdText] = useState('')
  const [cancelFeedback, setCancelFeedback] = useState('')
  const canRevise = Boolean(feedback.trim() || resumeVersion.trim() || jdText.trim().length >= 20)
  return <div>
    <p>匹配分数：{interrupt.score} / 阈值：{interrupt.threshold}</p>
    <ul>{interrupt.top_gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>
    {!revising ? <div className="actions-row">
      <button type="button" disabled={disabled} onClick={() => onSubmit({ action: 'continue' })}>继续</button>
      <label htmlFor="cancel-feedback">取消反馈（可选）</label><textarea id="cancel-feedback" value={cancelFeedback} onChange={(event) => setCancelFeedback(event.target.value)} disabled={disabled} />
      <button type="button" disabled={disabled} onClick={() => onSubmit(cancelFeedback.trim() ? { action: 'cancel', feedback: cancelFeedback.trim() } : { action: 'cancel' })}>取消</button>
      <button type="button" disabled={disabled} onClick={() => setRevising(true)}>修改输入后重新评审</button>
    </div> : <div>
      <label htmlFor="low-score-feedback">修订反馈</label><textarea id="low-score-feedback" value={feedback} onChange={(event) => setFeedback(event.target.value)} disabled={disabled} />
      <label htmlFor="resume-version">简历版本</label><input id="resume-version" value={resumeVersion} onChange={(event) => setResumeVersion(event.target.value)} disabled={disabled} />
      <label htmlFor="revised-jd">修正后的 JD</label><textarea id="revised-jd" value={jdText} onChange={(event) => setJdText(event.target.value)} disabled={disabled} />
      <button type="button" disabled={disabled || !canRevise} onClick={() => onSubmit({ action: 'revise_inputs', feedback: feedback.trim() || undefined, resume_version: resumeVersion.trim() || undefined, jd_text: jdText.trim() || undefined })}>提交修订</button>
    </div>}
  </div>
}