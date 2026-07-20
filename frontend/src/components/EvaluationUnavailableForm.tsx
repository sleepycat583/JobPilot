/** 面试评价不可用表单：仅允许重试或确认跳过当前题评价。 */
import type { EvaluationUnavailableCommand, ThreadInterrupt } from '../types'

export function EvaluationUnavailableForm({ interrupt, disabled, onSubmit }: { interrupt: Extract<ThreadInterrupt, { type: 'interview_evaluation_unavailable' }>; disabled: boolean; onSubmit: (command: EvaluationUnavailableCommand) => void }) {
  return <div>
    <p>题目 {interrupt.question_id} 的评价暂时不可用。</p>
    <button type="button" disabled={disabled} onClick={() => onSubmit({ action: 'retry_evaluation' })}>重新评价</button>
    <button type="button" disabled={disabled} onClick={() => { if (window.confirm('确认跳过本题评价吗？')) onSubmit({ action: 'skip_evaluation' }) }}>跳过评价</button>
  </div>
}