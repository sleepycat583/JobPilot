import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, newIdempotencyKey } from '../lib/api/client'
import type { ThreadState } from '../shared/types'

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
    }
    const events = ['node_started', 'message_completed', 'run_completed', 'run_failed', 'interrupt_required', 'run_resumed']
    events.forEach((name) => source.addEventListener(name, refresh))
    source.onopen = () => {
      setSseHealthy(true)
      setSseRetryDelay(5_000)
    }
    source.onerror = () => {
      setSseHealthy(false)
      setSseRetryDelay((current) => Math.min(current * 2, 30_000))
    }
    return () => source.close()
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
  }), [bootstrap.error, bootstrap.isLoading, selectedJDId, selectedResumeId, stateQuery, threadId])

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace() {
  const value = useContext(WorkspaceContext)
  if (!value) throw new Error('useWorkspace must be used inside WorkspaceProvider')
  return value
}
