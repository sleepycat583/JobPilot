import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, newIdempotencyKey } from '../lib/api/client'
import type { ThreadState } from '../shared/types'

type StreamingMessage = {
  id: string
  content: string
  targetContent: string
  completed: boolean
}

type WorkspaceValue = {
  threadId: string | null
  state: ThreadState | undefined
  loading: boolean
  error: Error | null
  selectedResumeId: string
  selectedJDId: string
  streamingMessage: StreamingMessage | null
  setSelectedResumeId: (id: string) => void
  setSelectedJDId: (id: string) => void
  refreshState: () => Promise<unknown>
  cancelActiveRun: () => Promise<ThreadState>
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null)
const THREAD_KEY = 'career-workspace-thread-id'
const CREATE_KEY = 'career-workspace-thread-create-key'

function getCreateKey() {
  const existing = sessionStorage.getItem(CREATE_KEY)
  if (existing) return existing
  const key = newIdempotencyKey()
  sessionStorage.setItem(CREATE_KEY, key)
  return key
}

/**
 * 解析 SSE 自定义事件，解析失败时返回 null，避免单条异常事件关闭整条连接。
 */
function parseEventData(event: Event): Record<string, unknown> | null {
  try {
    const data = JSON.parse((event as MessageEvent<string>).data)
    return data && typeof data === 'object' ? data as Record<string, unknown> : null
  } catch {
    return null
  }
}

function readString(data: Record<string, unknown>, key: string): string | null {
  return typeof data[key] === 'string' ? data[key] as string : null
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [storedThreadId] = useState(() => localStorage.getItem(THREAD_KEY))
  const bootstrap = useQuery({
    queryKey: ['thread-bootstrap', storedThreadId],
    queryFn: async () => {
      if (storedThreadId) return { thread_id: storedThreadId }
      const created = await api.createThread(getCreateKey())
      localStorage.setItem(THREAD_KEY, created.thread_id)
      return created
    },
    staleTime: Infinity,
    retry: 1,
  })
  const threadId = bootstrap.data?.thread_id ?? null
  const [sseHealthy, setSseHealthy] = useState(true)
  const [sseRetryDelay, setSseRetryDelay] = useState(5_000)
  const stateQuery = useQuery({
    queryKey: ['thread-state', threadId],
    queryFn: () => api.getThreadState(threadId!),
    enabled: Boolean(threadId),
    refetchInterval: sseHealthy ? false : sseRetryDelay,
  })
  const [selectedResumeId, setSelectedResumeId] = useState('')
  const [selectedJDId, setSelectedJDId] = useState('')
  const [streamingMessage, setStreamingMessage] = useState<StreamingMessage | null>(null)
  const refreshStreamingStateRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    if (!streamingMessage) return
    if (streamingMessage.content.length < streamingMessage.targetContent.length) {
      const timer = window.setTimeout(() => {
        setStreamingMessage((current) => {
          if (!current) return null
          const remaining = current.targetContent.length - current.content.length
          const step = Math.min(3, Math.max(1, Math.ceil(remaining / 10)))
          return { ...current, content: current.content + current.targetContent.slice(current.content.length, current.content.length + step) }
        })
      }, 24)
      return () => window.clearTimeout(timer)
    }
    if (streamingMessage.completed) {
      const timer = window.setTimeout(() => {
        setStreamingMessage(null)
        refreshStreamingStateRef.current?.()
      }, 120)
      return () => window.clearTimeout(timer)
    }
  }, [streamingMessage])

  useEffect(() => {
    if (stateQuery.data?.selected_resume_id) setSelectedResumeId(stateQuery.data.selected_resume_id)
    if (stateQuery.data?.selected_jd_id) setSelectedJDId(stateQuery.data.selected_jd_id)
  }, [stateQuery.data?.selected_jd_id, stateQuery.data?.selected_resume_id])

  useEffect(() => {
    if (!threadId) return
    const source = new EventSource(`/api/threads/${threadId}/events`)
    const refresh = () => {
      setSseHealthy(true)
      setSseRetryDelay(5_000)
      void queryClient.invalidateQueries({ queryKey: ['thread-state', threadId] })
      void queryClient.invalidateQueries({ queryKey: ['match-reports', threadId] })
      void queryClient.invalidateQueries({ queryKey: ['interview-history', threadId] })
    }
    refreshStreamingStateRef.current = refresh

    source.addEventListener('message_started', (event) => {
      const data = parseEventData(event)
      const messageId = data && readString(data, 'message_id')
      if (messageId) setStreamingMessage({ id: messageId, content: '', targetContent: '', completed: false })
    })

    source.addEventListener('message_delta', (event) => {
      const data = parseEventData(event)
      const messageId = data && readString(data, 'message_id')
      const delta = data && readString(data, 'delta')
      if (!messageId || !delta) return
      setStreamingMessage((prev) => {
        if (!prev || prev.id !== messageId) {
          return { id: messageId, content: '', targetContent: delta, completed: false }
        }
        return { ...prev, targetContent: prev.targetContent + delta }
      })
    })

    source.addEventListener('message_completed', (event) => {
      const data = parseEventData(event)
      const message = data?.message && typeof data.message === 'object' ? data.message as Record<string, unknown> : null
      const messageId = (data && readString(data, 'message_id')) ?? (message && readString(message, 'id'))
      const finalContent = message && readString(message, 'content')
      if (!messageId || !finalContent) {
        refresh()
        return
      }
      setStreamingMessage((prev) => ({
        id: messageId,
        content: prev?.id === messageId ? prev.content : '',
        targetContent: finalContent,
        completed: true,
      }))
    })

    const clearStreamingMessage = () => {
      setStreamingMessage(null)
      refresh()
    }
    const events = ['node_started', 'interrupt_required', 'run_resumed']
    events.forEach((name) => source.addEventListener(name, refresh))
    source.addEventListener('run_failed', clearStreamingMessage)
    source.addEventListener('run_cancelled', clearStreamingMessage)
    source.onopen = () => {
      setSseHealthy(true)
      setSseRetryDelay(5_000)
    }
    source.onerror = () => {
      setSseHealthy(false)
      setSseRetryDelay((current) => Math.min(current * 2, 30_000))
    }
    return () => {
      source.close()
      refreshStreamingStateRef.current = null
    }
  }, [queryClient, threadId])

  const value = useMemo<WorkspaceValue>(() => ({
    threadId,
    state: stateQuery.data,
    loading: bootstrap.isLoading || stateQuery.isLoading,
    error: (bootstrap.error ?? stateQuery.error) as Error | null,
    selectedResumeId,
    selectedJDId,
    streamingMessage,
    setSelectedResumeId,
    setSelectedJDId,
    refreshState: () => stateQuery.refetch(),
    cancelActiveRun: async () => {
      if (!threadId) throw new Error('会话尚未准备好')
      const next = await api.cancelThread(threadId)
      queryClient.setQueryData(['thread-state', threadId], next)
      return next
    },
  }), [bootstrap.error, bootstrap.isLoading, queryClient, selectedJDId, selectedResumeId, stateQuery, streamingMessage, threadId])

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace() {
  const value = useContext(WorkspaceContext)
  if (!value) throw new Error('useWorkspace must be used inside WorkspaceProvider')
  return value
}
