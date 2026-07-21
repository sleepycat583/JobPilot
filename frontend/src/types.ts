/**
 * 前端运行时状态与 SSE 契约类型。
 *
 * 做什么：
 * - 统一声明 Task 5/6 需要的任务状态、后端 SSE 事件和任务启动响应。
 * - 供 reducer、Hook 和页面组件共享，避免各处手写字段名。
 */

export type RunStatus =
  | 'idle'
  | 'running'
  | 'interrupted'
  | 'resuming'
  | 'completed'
  | 'failed'

export type AgentEventName =
  | 'node_started'
  | 'node_finished'
  | 'interrupt_required'
  | 'node_retrying'
  | 'run_resumed'
  | 'run_completed'
  | 'run_failed'

export type AgentEvent = {
  event: AgentEventName
  event_id: string
  session_id: string
  thread_id: string
  timestamp: string
  node: string | null
  node_kind: string | null
  node_run_id: string | null
  started_at: string | null
  ended_at: string | null
  duration_ms: number | null
  input_summary: string
  detail?: string
  error_code: string | null
  success: boolean | null
  session_sequence?: number
}

export type TaskAcceptedResponse = {
  session_id: string
  thread_id: string
  status: 'accepted'
}

export type AgentProgressState = {
  sessionId: string | null
  threadId: string | null
  status: RunStatus
  currentNode: string | null
  completedNodes: string[]
  events: AgentEvent[]
  lastEventId: string | null
  activeEventSourceSession: string | null
  errorMessage: string | null
}

type InterruptBase = {
  target: string
  accepted_actions: string[]
}

export type ThreadInterrupt =
  | (InterruptBase & {
  type: 'final_review'
  target: 'jd_parsed' | 'match_result' | 'interview_report'
  accepted_actions: ('approve' | 'reject')[]
  draft?: Record<string, unknown>
})
  | (InterruptBase & {
  type: 'low_match_score'
  target: 'match_result'
  accepted_actions: ('continue' | 'revise_inputs' | 'cancel')[]
  score: number
  threshold: number
  top_gaps: string[]
})
  | (InterruptBase & {
  type: 'interview_answer'
  target: 'interview_state'
  accepted_actions: ('submit_answer' | 'context_update' | 'end_interview')[]
  question_id: string
  question: string
})
  | (InterruptBase & {
  type: 'interview_evaluation_unavailable'
  target: 'question_record'
  accepted_actions: ('retry_evaluation' | 'skip_evaluation')[]
  question_id: string
})

export type FinalReviewCommand =
  | { action: 'approve' }
  | { action: 'reject'; feedback: string }
export type LowScoreReviewCommand =
  | { action: 'continue'; feedback?: string }
  | { action: 'revise_inputs'; feedback?: string; resume_version?: string; jd_text?: string }
  | { action: 'cancel'; feedback?: string }
export type InterviewAnswerCommand =
  | { action: 'submit_answer'; answer: string; context?: string }
  | { action: 'context_update'; context: string; answer?: string }
  | { action: 'end_interview' }
export type EvaluationUnavailableCommand =
  | { action: 'retry_evaluation' }
  | { action: 'skip_evaluation' }
export type ThreadReviewCommand =
  | FinalReviewCommand
  | LowScoreReviewCommand
  | InterviewAnswerCommand
  | EvaluationUnavailableCommand

export type ThreadStateResponse = {
  thread_id: string
  session_id: string
  status: 'interrupted' | 'completed'
  review_status: string | null
  review_target: string | null
  current_node: string | null
  interrupt: ThreadInterrupt | null
}

/** POST /api/tasks 和 POST /v1/job-analysis 共用请求体 */
export type JobAnalysisRequest = {
  jd_text: string
  resume_version?: string
}

/** 后端统一错误响应格式（第 0 章全局约束） */
export type ApiErrorResponse = {
  error: {
    code: string
    message: string
  }
}