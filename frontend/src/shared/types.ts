export type View = 'workbench' | 'resumes' | 'jd' | 'match' | 'interview'
export type TaskStatus = 'idle' | 'running' | 'interrupted' | 'completed' | 'failed'

export type MessageItem = {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export type TaskStep = { label: string; status: 'pending' | 'active' | 'done' }
export type TaskView = { status: TaskStatus; title: string; detail: string; steps: TaskStep[] }

export type InterruptPayload = {
  id: string
  type: 'low_match_score' | 'interview_answer' | 'interview_continue' | 'final_review'
  title: string
  detail: string
  accepted_actions: string[]
  data: Record<string, unknown>
}

export type ThreadState = {
  thread_id: string
  status: TaskStatus
  messages: MessageItem[]
  task: TaskView
  selected_resume_id: string | null
  selected_jd_id: string | null
  match_result: MatchResult | null
  interview: InterviewState | null
  pending_interrupt: InterruptPayload | null
  updated_at: string
}

export type JobRead = {
  id: string
  kind: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  result: Record<string, unknown> | null
  error: { code: string; message: string; retryable: boolean } | null
  created_at: string
  updated_at: string
}

export type JobAccepted = { job_id: string; resource_id: string | null; status: string; status_url: string }

export type MatchReportHistory = {
  id: string
  thread_id: string
  resume_id: string
  jd_id: string
  strict: boolean
  result: MatchResult
  created_at: string
}

export type InterviewHistory = {
  id: string
  thread_id: string
  resume_id: string
  jd_id: string
  interview_type: string
  question_count: number
  overall_score: number | null
  result: InterviewState
  created_at: string
  completed_at: string
}

export type ResumeRead = {
  id: string
  version: number
  display_name: string
  file_name: string
  content_type: string
  size_bytes: number
  status: string
  structured: null | {
    profile?: string
    target_role?: string
    years?: number
    education?: string
    locations?: string[]
    skills?: string[]
    chunk_count?: number
    privacy_filtered?: boolean
    experience?: Array<{
      company: string
      role: string
      period: string
      highlights: string[]
      technologies: string[]
      evidence: string
    }>
    projects?: Array<{
      name: string
      role: string
      period: string
      highlights: string[]
      technologies: string[]
      evidence: string
    }>
  }
  created_at: string
  updated_at: string
}

export type JDRead = {
  id: string
  title: string
  source_text: string
  status: string
  parsed: null | {
    job_title?: string
    seniority?: string
    responsibilities?: string[]
    required_skills?: string[]
    preferred_skills?: string[]
    inferred_skills?: string[]
    interview_focus?: string[]
    evidence_preserved?: boolean
  }
  created_at: string
  updated_at: string
}

export type MatchResult = {
  total_score: number
  dimension_scores: Record<string, number>
  strengths: string[]
  gaps: string[]
  evidence_count: number
  low_score_review_required: boolean
  evidence?: Array<{
    requirement: string
    resume_evidence: string
    assessment: string
    status: string
  }>
}

export type InterviewState = {
  run_id: string
  interview_type: '综合面试' | '技术专项' | '项目深挖'
  question_count: number
  feedback_mode: 'each' | 'final'
  current_index: number
  phase: 'question' | 'feedback' | 'report'
  current_question: string
  records: Array<{ question: string; answer: string; score: number }>
  feedback: null | { score: number; title: string; detail: string; tags: string[] }
  report: null | {
    overall_score: number
    summary: string
    dimension_scores: Record<string, number>
    actions: string[]
  }
}
