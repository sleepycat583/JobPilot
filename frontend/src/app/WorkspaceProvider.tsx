import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, newIdempotencyKey } from '../lib/api/client'
import type { MessageItem, StreamDraft, ThreadState } from '../shared/types'

type WorkspaceValue = {
  threadId: string | null
  state: ThreadState | undefined
  loading: boolean
  error: Error | null
  selectedResumeId: string
  selectedJDId: string
  setSelectedResumeId: (id: string) => void
  setSelectedJDId: (id: string) => void
  refreshState: () => Promise<unknown>
  cancelActiveRun: () => Promise<ThreadState>
  streamDraft: StreamDraft | null
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
  const [streamDraft, setStreamDraft] = useState<StreamDraft | null>(null)
  const knownMessageIds = useRef<Set<string>>(new Set())

  useEffect(() => {
    knownMessageIds.current = new Set(stateQuery.data?.messages.map((message) => message.id) ?? [])
  }, [stateQuery.data?.messages])

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
    const parse = (event: MessageEvent<string>) => {
      try {
        return JSON.parse(event.data) as Record<string, unknown>
      } catch {
        return null
      }
    }
    const onStarted = (event: Event) => {
      const data = parse(event as MessageEvent<string>)
      const runId = typeof data?.run_id === 'string' ? data.run_id : ''
      const messageId = typeof data?.message_id === 'string' ? data.message_id : ''
      if (!runId || !messageId || knownMessageIds.current.has(messageId)) return
      setStreamDraft({ runId, messageId, content: '', lastIndex: -1 })
      refresh()
    }
    const onDelta = (event: Event) => {
      const data = parse(event as MessageEvent<string>)
      const runId = typeof data?.run_id === 'string' ? data.run_id : ''
      const messageId = typeof data?.message_id === 'string' ? data.message_id : ''
      const index = typeof data?.index === 'number' ? data.index : -1
      const delta = typeof data?.delta === 'string' ? data.delta : ''
      if (!runId || !messageId || !delta || index < 0) return
      setStreamDraft((current) => {
        if (!current || current.runId !== runId || current.messageId !== messageId) return current
        if (index <= current.lastIndex) return current
        return { ...current, content: current.content + delta, lastIndex: index }
      })
    }
    const onCompleted = (event: Event) => {
      const data = parse(event as MessageEvent<string>)
      const message = data?.message as MessageItem | undefined
      if (message?.id) knownMessageIds.current.add(message.id)
      setStreamDraft((current) => {
        if (message && current?.messageId === message.id) return null
        return current
      })
      refresh()
    }
    const clearDraft = () => setStreamDraft(null)
    source.addEventListener('message_started', onStarted)
    source.addEventListener('message_delta', onDelta)
    source.addEventListener('message_completed', onCompleted)
    const events = ['node_started', 'run_completed', 'interrupt_required', 'run_resumed']
    events.forEach((name) => source.addEventListener(name, refresh))
    source.addEventListener('run_failed', clearDraft)
    source.addEventListener('run_cancelled', clearDraft)
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
      setStreamDraft(null)
    }
  }, [queryClient, threadId])

  const value = useMemo<WorkspaceValue>(() => ({
    threadId,
    state: stateQuery.data,
    loading: bootstrap.isLoading || stateQuery.isLoading,
    error: (bootstrap.error ?? stateQuery.error) as Error | null,
    selectedResumeId,
    selectedJDId,
    setSelectedResumeId,
    setSelectedJDId,
    refreshState: () => stateQuery.refetch(),
    cancelActiveRun: async () => {
      if (!threadId) throw new Error('会话尚未准备好')
      const next = await api.cancelThread(threadId)
      queryClient.setQueryData(['thread-state', threadId], next)
      return next
    },
    streamDraft,
  }), [bootstrap.error, bootstrap.isLoading, queryClient, selectedJDId, selectedResumeId, stateQuery, streamDraft, threadId])

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace() {
  const value = useContext(WorkspaceContext)
  if (!value) throw new Error('useWorkspace must be used inside WorkspaceProvider')
  return value
}
