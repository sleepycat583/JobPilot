/** 刷新恢复与 resume 幂等状态。
 *
 * 独立于 SSE 进度状态：它从 state API 恢复当前 interrupt，并在真实 resume HTTP
 * 响应返回之前保持 `isResuming`，以阻止重复提交。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ThreadReviewCommand, ThreadStateResponse } from '../types'

const STORAGE_KEY = 'job-assistant.active-thread'

type StoredThread = { threadId: string; sessionId: string }
type ReviewApiError = Error & { code?: string }

function buildApiError(payload: unknown, fallback: string): ReviewApiError {
  const apiError = (payload as { error?: { code?: string; message?: string } } | null)?.error
  return Object.assign(new Error(apiError?.message ?? fallback), { code: apiError?.code })
}

function readStoredThread(): StoredThread | null {
  const raw = sessionStorage.getItem(STORAGE_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as StoredThread
  } catch {
    sessionStorage.removeItem(STORAGE_KEY)
    return null
  }
}

export function useThreadReview(threadId: string | null, sessionId: string | null) {
  const [state, setState] = useState<ThreadStateResponse | null>(null)
  const [isResuming, setIsResuming] = useState(false)
  const [error, setError] = useState<{ code?: string; message: string } | null>(null)
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null)
  const lastCommandRef = useRef<ThreadReviewCommand | null>(null)
  const interruptFingerprintRef = useRef<string | null>(null)

  const applyState = useCallback((payload: ThreadStateResponse) => {
    const fingerprint = payload.interrupt ? JSON.stringify(payload.interrupt) : null
    setState(payload)
    setIdempotencyKey((currentKey) => {
      if (!fingerprint) {
        interruptFingerprintRef.current = null
        return null
      }
      if (interruptFingerprintRef.current === fingerprint && currentKey) return currentKey
      interruptFingerprintRef.current = fingerprint
      return crypto.randomUUID()
    })
  }, [])

  const loadState = useCallback(async (id: string) => {
    const response = await fetch(`/v1/threads/${id}/state`)
    if (!response.ok) {
      const error = buildApiError(await response.json().catch(() => null), '无法恢复当前审核状态。')
      if (error.code === 'CHECKPOINT_NOT_FOUND' || response.status === 404) {
        setState(null)
        setIdempotencyKey(null)
        interruptFingerprintRef.current = null
      }
      throw error
    }
    const payload = (await response.json()) as ThreadStateResponse
    applyState(payload)
    return payload
  }, [applyState])

  useEffect(() => {
    const active = threadId && sessionId ? { threadId, sessionId } : readStoredThread()
    if (!active) return
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(active))
    // 通过异步回调读取远端状态，避免 effect 同步派生 React 状态。
    const timer = window.setTimeout(() => {
      void loadState(active.threadId).catch((reason: unknown) => {
        setError({ message: reason instanceof Error ? reason.message : '恢复状态失败。' })
      })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [loadState, sessionId, threadId])

  const resume = useCallback(async (command: ThreadReviewCommand) => {
    if (!state?.interrupt || !idempotencyKey || isResuming) return
    lastCommandRef.current = command
    setIsResuming(true)
    setError(null)
    try {
      const response = await fetch(`/v1/threads/${state.thread_id}/resume`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idempotency_key: idempotencyKey, command }),
      })
      if (!response.ok) {
        throw buildApiError(await response.json().catch(() => null), '审核提交失败。')
      }
      const next = (await response.json()) as ThreadStateResponse
      applyState(next)
    } catch (reason) {
      setError({ code: reason instanceof Error ? (reason as ReviewApiError).code : undefined, message: reason instanceof Error ? reason.message : '审核提交失败。' })
    } finally {
      setIsResuming(false)
    }
  }, [applyState, idempotencyKey, isResuming, state])

  const retry = useCallback(() => {
    if (lastCommandRef.current) void resume(lastCommandRef.current)
  }, [resume])

  return { state, isResuming, error, idempotencyKey, resume, retry, loadState }
}