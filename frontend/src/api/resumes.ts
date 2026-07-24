/** 简历库 HTTP 调用，统一处理后端 { error: { code, message } } 错误协议。 */
import type { ApiErrorResponse, ResumeDto, ResumeListResponse } from '../types'

export class ResumeApiError extends Error {
  readonly code?: string

  constructor(message: string, code?: string) {
    super(message)
    this.name = 'ResumeApiError'
    this.code = code
  }
}

async function parseResponse<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json().catch(() => null) as T | ApiErrorResponse | null
  if (!response.ok) {
    const apiError = (body as ApiErrorResponse | null)?.error
    throw new ResumeApiError(apiError?.message ?? `${fallback} (HTTP ${response.status})`, apiError?.code)
  }
  return body as T
}

/** 获取全部简历版本，供页面初次加载和轮询结束后同步。 */
export async function listResumes(): Promise<ResumeDto[]> {
  const response = await fetch('/v1/resumes')
  return (await parseResponse<ResumeListResponse>(response, '读取简历库失败')).resumes
}

/** 获取一个简历版本的最新索引状态。 */
export async function getResume(resumeId: string): Promise<ResumeDto> {
  const response = await fetch(`/v1/resumes/${encodeURIComponent(resumeId)}`)
  return parseResponse<ResumeDto>(response, '读取简历索引状态失败')
}

/** 提交一个新的 TXT 简历版本；幂等键由调用方在网络重试时复用。 */
export async function uploadResume(file: File, idempotencyKey: string): Promise<ResumeDto> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch('/v1/resumes', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: formData,
  })
  return parseResponse<ResumeDto>(response, '上传简历失败')
}

/** 请求后端为失败版本重新建立索引。 */
export async function retryResumeIndex(resumeId: string): Promise<ResumeDto> {
  const response = await fetch(`/v1/resumes/${encodeURIComponent(resumeId)}/retry`, { method: 'POST' })
  return parseResponse<ResumeDto>(response, '重试索引失败')
}