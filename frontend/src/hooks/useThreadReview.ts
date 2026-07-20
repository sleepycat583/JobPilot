/** 刷新恢复与 resume 幂等状态。
 *
 * 独立于 SSE 进度状态：它从 state API 恢复当前 interrupt，并在真实 resume HTTP
 * 响应返回之前保持 `isResuming`，以阻止重复提交。
 */
import { useCallback, useEffect, useState } from 'react'

import type { ThreadStateResponse } from '../types'

const STORAGE_KEY = 'job-assistant.active-thread'

type StoredThread = { threadId: string; sessionId: string }

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
  const [error, setError] = useState<string | null>(null)
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null)

  const loadState = useCallback(async (id: string) => {
    const response = await fetch(`/v1/threads/${id}/state`)
    if (!response.ok) throw new Error('无法恢复当前审核状态。')
    const payload = (await response.json()) as ThreadStateResponse
    setState(payload)
    setIdempotencyKey(payload.interrupt ? crypto.randomUUID() : null)
    return payload
  }, [])

  useEffect(() => {
    const active = threadId && sessionId ? { threadId, sessionId } : readStoredThread()
    if (!active) return
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(active))
    // 通过异步回调读取远端状态，避免 effect 同步派生 React 状态。
    const timer = window.setTimeout(() => {
      void loadState(active.threadId).catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : '恢复状态失败。')
      })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [loadState, sessionId, threadId])

  const resume = useCallback(async (command: Record<string, string>) => {
    if (!state?.interrupt || !idempotencyKey || isResuming) return
    setIsResuming(true)
    setError(null)
    try {
      const response = await fetch(`/v1/threads/${state.thread_id}/resume`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idempotency_key: idempotencyKey, command }),
      })
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.error?.message ?? '审核提交失败。')
      const next = (await response.json()) as ThreadStateResponse
      setState(next)
      setIdempotencyKey(next.interrupt ? crypto.randomUUID() : null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '审核提交失败。')
    } finally {
      setIsResuming(false)
    }
  }, [idempotencyKey, isResuming, state])

  return { state, isResuming, error, resume, loadState }
}