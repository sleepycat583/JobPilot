import type { JDRead, JobAccepted, JobRead, ResumeRead, ThreadState } from '../../shared/types'

type ApiErrorBody = { error?: { code?: string; message?: string; retryable?: boolean } }

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly retryable: boolean,
  ) {
    super(message)
  }
}

export const newIdempotencyKey = () => crypto.randomUUID()

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody
    throw new ApiError(
      body.error?.message ?? `请求失败 (${response.status})`,
      response.status,
      body.error?.code ?? 'HTTP_ERROR',
      body.error?.retryable ?? false,
    )
  }
  return response.json() as Promise<T>
}

const jsonHeaders = (key?: string) => ({
  'Content-Type': 'application/json',
  ...(key ? { 'Idempotency-Key': key } : {}),
})

export const api = {
  createThread: (key: string) => request<{ thread_id: string; state_url: string; events_url: string }>('/api/threads', { method: 'POST', headers: { 'Idempotency-Key': key } }),
  getThreadState: (threadId: string) => request<ThreadState>(`/api/threads/${threadId}/state`),
  sendMessage: (threadId: string, body: { content: string; resume_id?: string; jd_id?: string }, key = newIdempotencyKey()) => request(`/api/threads/${threadId}/messages`, { method: 'POST', headers: jsonHeaders(key), body: JSON.stringify(body) }),
  resumeThread: (threadId: string, body: { interrupt_id: string; action: string; payload?: Record<string, unknown> }, key = newIdempotencyKey()) => request<ThreadState>(`/api/threads/${threadId}/resume`, { method: 'POST', headers: jsonHeaders(key), body: JSON.stringify({ ...body, payload: body.payload ?? {} }) }),
  listResumes: () => request<ResumeRead[]>('/api/resumes'),
  getResume: (resumeId: string) => request<ResumeRead>(`/api/resumes/${resumeId}`),
  uploadResume: (file: File, key = newIdempotencyKey()) => {
    const form = new FormData()
    form.append('file', file)
    return request<JobAccepted>('/api/resumes', { method: 'POST', headers: { 'Idempotency-Key': key }, body: form })
  },
  listJDs: () => request<JDRead[]>('/api/jds'),
  getJD: (jdId: string) => request<JDRead>(`/api/jds/${jdId}`),
  createJD: (text: string, key = newIdempotencyKey()) => request<JobAccepted>('/api/jds', { method: 'POST', headers: jsonHeaders(key), body: JSON.stringify({ text }) }),
  getJob: (jobId: string) => request<JobRead>(`/api/jobs/${jobId}`),
  retryJob: (jobId: string, key = newIdempotencyKey()) => request<JobAccepted>(`/api/jobs/${jobId}/retry`, { method: 'POST', headers: { 'Idempotency-Key': key } }),
  createMatch: (body: { thread_id: string; resume_id: string; jd_id: string; strict: boolean }, key = newIdempotencyKey()) => request('/api/matches', { method: 'POST', headers: jsonHeaders(key), body: JSON.stringify(body) }),
  startInterview: (body: { thread_id: string; resume_id: string; jd_id: string; interview_type: string; question_count: number; feedback_mode: string }, key = newIdempotencyKey()) => request('/api/interviews', { method: 'POST', headers: jsonHeaders(key), body: JSON.stringify(body) }),
}

export async function pollJob(jobId: string, onUpdate?: (job: JobRead) => void): Promise<JobRead> {
  const deadline = Date.now() + 30_000
  let delay = 400
  while (Date.now() < deadline) {
    const job = await api.getJob(jobId)
    onUpdate?.(job)
    if (job.status === 'completed') return job
    if (job.status === 'failed') throw new ApiError(job.error?.message ?? '后台任务失败', 500, job.error?.code ?? 'JOB_FAILED', job.error?.retryable ?? false)
    await new Promise((resolve) => window.setTimeout(resolve, delay))
    delay = Math.min(Math.round(delay * 1.7), 3_000)
  }
  throw new ApiError('后台任务等待超时，请稍后重试。', 408, 'JOB_TIMEOUT', true)
}
