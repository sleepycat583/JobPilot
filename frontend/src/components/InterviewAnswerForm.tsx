/** 面试回答表单：提交回答、补充上下文或确认结束当前面试。 */
import { useState } from 'react'
import type { InterviewAnswerCommand, ThreadInterrupt } from '../types'

export function InterviewAnswerForm({ interrupt, disabled, onSubmit }: { interrupt: Extract<ThreadInterrupt, { type: 'interview_answer' }>; disabled: boolean; onSubmit: (command: InterviewAnswerCommand) => void }) {
  const [answer, setAnswer] = useState('')
  const [context, setContext] = useState('')
  const [reviseAnswer, setReviseAnswer] = useState(false)
  return <div>
    <p>{interrupt.question}</p>
    <label htmlFor="interview-answer">回答</label><textarea id="interview-answer" value={answer} onChange={(event) => setAnswer(event.target.value)} disabled={disabled} />
    <button type="button" disabled={disabled || !answer.trim()} onClick={() => onSubmit({ action: 'submit_answer', answer: answer.trim() })}>提交回答</button>
    <label htmlFor="interview-context">补充或更正背景</label><textarea id="interview-context" value={context} onChange={(event) => setContext(event.target.value)} disabled={disabled} />
    <label><input type="checkbox" checked={reviseAnswer} onChange={(event) => setReviseAnswer(event.target.checked)} disabled={disabled} />同时修订当前答案</label>
    <button type="button" disabled={disabled || !context.trim() || (reviseAnswer && !answer.trim())} onClick={() => onSubmit(reviseAnswer ? { action: 'context_update', context: context.trim(), answer: answer.trim() } : { action: 'context_update', context: context.trim() })}>提交补充</button>
    <button type="button" disabled={disabled} onClick={() => { if (window.confirm('确认结束面试吗？')) onSubmit({ action: 'end_interview' }) }}>结束面试</button>
  </div>
}