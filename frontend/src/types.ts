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
  nodeProgress: Record<string, 'pending' | 'running' | 'completed' | 'interrupted' | 'failed'>
}

/** 后端 JDParsed 的前端只读映射；字段名与冻结的 Pydantic Schema 保持一致。 */
export type JDParsed = {
  job_title: string
  seniority: string
  company_name: string | null
  responsibilities: string[]
  skills: { name: string; category: string; priority: string; evidence: string }[]
  experience_requirements: string[]
  education_requirements: string[]
  interview_focus: string[]
  company_context: string[]
  ambiguities: string[]
  source_language: string
}

/** 后端 MatchResult 的前端只读映射，包含可追溯到简历片段的证据。 */
export type MatchEvidence = {
  chunk_id: string
  quote: string
  relevance: number
}

export type MatchItem = {
  requirement: string
  status: 'matched' | 'transferable' | 'weak' | 'missing'
  score: number
  evidence: MatchEvidence[]
  rationale: string
}

export type MatchResult = {
  total_score: number
  dimension_scores: Record<string, number>
  matched_items: MatchItem[]
  strengths: string[]
  gaps: string[]
  recommendations: string[]
  low_score_review_required: boolean
  resume_id: string
}

export type MatchUnavailableResult = {
  status: 'MATCH_UNAVAILABLE'
  resume_id: string
  retrieval_evidence: { requirement: string; evidence: MatchEvidence[] }[]
  message: string
}

export type MatchAnalysis = MatchResult | MatchUnavailableResult

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
  | { action: 'revise_inputs'; feedback?: string; resume_id?: string; jd_text?: string }
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
  status: 'running' | 'interrupted' | 'completed' | 'failed'
  review_status: string | null
  review_target: string | null
  current_node: string | null
  interrupt: ThreadInterrupt | null
  jd_parsed?: JDParsed | null
  match_result?: MatchAnalysis | null
  final_output?: Record<string, unknown> | null
}

/** POST /api/tasks 和 POST /v1/job-analysis 共用请求体 */
export type JobAnalysisRequest = {
  jd_text: string
  resume_id?: string
}

/** 后端统一错误响应格式（第 0 章全局约束） */
export type ApiErrorResponse = {
  error: {
    code: string
    message: string
  }
}

/** 简历库接口的索引状态；状态完全由后端返回，前端不得自行推断。 */
export type ResumeIndexStatus = 'pending' | 'indexing' | 'indexed' | 'failed'

/** 已冻结的简历版本 DTO，对应 /v1/resumes 的列表项和单项响应。 */
export type ResumeDto = {
  resume_id: string
  display_version: number
  file_name: string
  file_size: number
  created_at: string
  updated_at: string
  index_status: ResumeIndexStatus
  error_code: string | null
  error_message: string | null
}

export type ResumeListResponse = {
  resumes: ResumeDto[]
}