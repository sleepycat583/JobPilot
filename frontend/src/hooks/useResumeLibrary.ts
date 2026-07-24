/** 简历库状态管理：加载、上传、索引轮询与失败重试。 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { getResume, listResumes, retryResumeIndex, uploadResume } from '../api/resumes'
import type { ResumeDto } from '../types'

const MAX_RESUME_FILE_SIZE = 2 * 1024 * 1024
const POLL_INTERVAL_MS = 1000
const MAX_POLL_ATTEMPTS = 120
const MAX_NETWORK_RETRIES = 3

function replaceResume(resumes: ResumeDto[], next: ResumeDto): ResumeDto[] {
  const index = resumes.findIndex((resume) => resume.resume_id === next.resume_id)
  if (index < 0) return [next, ...resumes]
  return resumes.map((resume) => resume.resume_id === next.resume_id ? next : resume)
}

function validateUploadFile(file: File): string | null {
  if (!file.name.toLowerCase().endsWith('.txt')) return '仅支持上传 UTF-8 编码的 .txt 简历。'
  if (file.size > MAX_RESUME_FILE_SIZE) return '简历文件不能超过 2 MB。'
  return null
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

/**
 * 管理简历资源及其异步索引状态。
 *
 * 返回值包含仅允许选择已索引简历的选择方法，以及上传、重试和手动刷新操作。
 */
export function useResumeLibrary() {
  const [resumes, setResumes] = useState<ResumeDto[]>([])
  const [selectedResumeId, setSelectedResumeId] = useState<string>('')
  const [isLoading, setIsLoading] = useState(true)
  const [isUploading, setIsUploading] = useState(false)
  const [retryingResumeId, setRetryingResumeId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const refresh = useCallback(async () => {
    setError(null)
    setIsLoading(true)
    try {
      const next = await listResumes()
      if (!mountedRef.current) return
      setResumes(next)
      // 用户必须明确选择简历；自动选中会让“仅解析 JD”的请求意外进入匹配流程。
      setSelectedResumeId((current) => current && next.some((resume) => resume.resume_id === current && resume.index_status === 'indexed') ? current : '')
    } catch (cause: unknown) {
      if (mountedRef.current) setError(cause instanceof Error ? cause.message : '读取简历库失败，请刷新重试。')
    } finally {
      if (mountedRef.current) setIsLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  /** 轮询单份简历直到进入终态；网络短暂失败最多允许三次。 */
  const pollIndexStatus = useCallback(async (resumeId: string) => {
    let networkFailures = 0
    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS && mountedRef.current; attempt += 1) {
      await wait(POLL_INTERVAL_MS)
      try {
        const next = await getResume(resumeId)
        networkFailures = 0
        if (!mountedRef.current) return
        setResumes((current) => replaceResume(current, next))
        if (next.index_status === 'indexed') return
        if (next.index_status === 'failed') return
      } catch (cause: unknown) {
        networkFailures += 1
        if (networkFailures >= MAX_NETWORK_RETRIES && mountedRef.current) {
          setError(cause instanceof Error ? cause.message : '索引状态查询失败，请手动刷新。')
          return
        }
      }
    }
    if (mountedRef.current) setError('索引状态查询超时，请稍后手动刷新。')
  }, [])

  /** 校验并上传新文件；同一次提交失败后的网络重试复用同一幂等键。 */
  const upload = useCallback(async (file: File) => {
    const validationMessage = validateUploadFile(file)
    if (validationMessage) {
      setError(validationMessage)
      return
    }
    setError(null)
    setIsUploading(true)
    try {
      const created = await uploadResume(file, crypto.randomUUID())
      if (!mountedRef.current) return
      setResumes((current) => replaceResume(current, created))
      void pollIndexStatus(created.resume_id)
    } catch (cause: unknown) {
      if (mountedRef.current) setError(cause instanceof Error ? cause.message : '上传简历失败，请重试。')
    } finally {
      if (mountedRef.current) setIsUploading(false)
    }
  }, [pollIndexStatus])

  /** 只允许失败版本重新建立索引，避免重复提交正在执行的后台任务。 */
  const retry = useCallback(async (resumeId: string) => {
    setError(null)
    setRetryingResumeId(resumeId)
    try {
      const accepted = await retryResumeIndex(resumeId)
      if (!mountedRef.current) return
      setResumes((current) => replaceResume(current, accepted))
      void pollIndexStatus(resumeId)
    } catch (cause: unknown) {
      if (mountedRef.current) setError(cause instanceof Error ? cause.message : '重试索引失败，请稍后再试。')
    } finally {
      if (mountedRef.current) setRetryingResumeId(null)
    }
  }, [pollIndexStatus])

  const select = useCallback((resume: ResumeDto) => {
    if (resume.index_status === 'indexed') setSelectedResumeId(resume.resume_id)
  }, [])

  /** 清除当前选择，使后续 JD 解析请求不会携带 resume_id。 */
  const clearSelection = useCallback(() => setSelectedResumeId(''), [])

  return { resumes, selectedResumeId, isLoading, isUploading, retryingResumeId, error, refresh, upload, retry, select, clearSelection }
}