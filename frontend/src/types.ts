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

export type ThreadInterrupt = {
  type: 'final_review' | 'low_match_score' | 'interview_answer' | 'interview_evaluation_unavailable'
  target: string
  accepted_actions: string[]
  draft?: Record<string, unknown>
  question?: string
  question_id?: string
  score?: number
  threshold?: number
  top_gaps?: string[]
}

export type ThreadStateResponse = {
  thread_id: string
  session_id: string
  status: 'interrupted' | 'completed'
  review_status: string | null
  review_target: string | null
  current_node: string | null
  interrupt: ThreadInterrupt | null
}