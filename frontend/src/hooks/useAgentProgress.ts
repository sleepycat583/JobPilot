import { useEffect, useMemo, useReducer, useRef } from 'react'

import type { AgentEvent, AgentEventName, AgentProgressState } from '../types'

type ProgressAction =
  | { type: 'connect'; sessionId: string; threadId: string }
  | { type: 'event'; payload: AgentEvent }
  | { type: 'error'; message: string }
  | { type: 'reset' }

const EVENT_NAMES: AgentEventName[] = [
  'node_started',
  'node_finished',
  'interrupt_required',
  'node_retrying',
  'run_resumed',
  'run_completed',
  'run_failed',
]

const initialState: AgentProgressState = {
  sessionId: null,
  threadId: null,
  status: 'idle',
  currentNode: null,
  completedNodes: [],
  events: [],
  lastEventId: null,
  activeEventSourceSession: null,
  errorMessage: null,
}

/**
 * 根据后端事件推进前端执行状态。
 *
 * 关键参数：
 * - state: 当前前端进度状态。
 * - action: 连接、事件或错误动作。
 *
 * 返回值：
 * - 更新后的 UI 状态快照。
 */
function reducer(
  state: AgentProgressState,
  action: ProgressAction,
): AgentProgressState {
  if (action.type === 'reset') {
    return initialState
  }

  if (action.type === 'connect') {
    return {
      sessionId: action.sessionId,
      threadId: action.threadId,
      activeEventSourceSession: action.sessionId,
      status: 'running',
      currentNode: null,
      completedNodes: [],
      events: [],
      lastEventId: null,
      errorMessage: null,
    }
  }

  if (action.type === 'error') {
    return {
      ...state,
      errorMessage: action.message,
      status: state.status === 'completed' ? state.status : 'failed',
      activeEventSourceSession: null,
    }
  }

  const event = action.payload
  // SSE 是 session 级通道。当前页面只展示本次提交的 thread，回放或并行任务事件
  // 不能改变该任务的进度状态。
  if (event.thread_id !== state.threadId || state.events.some((item) => item.event_id === event.event_id)) {
    return state
  }
  const completedNodes = [...state.completedNodes]
  const eventNode = event.node ?? null

  if (event.event === 'node_finished' && eventNode && !completedNodes.includes(eventNode)) {
    completedNodes.push(eventNode)
  }

  let status = state.status
  let currentNode = state.currentNode

  switch (event.event) {
    case 'node_started':
      status = 'running'
      currentNode = eventNode
      break
    case 'node_finished':
      status = 'running'
      currentNode = null
      break
    case 'node_retrying':
      status = 'running'
      currentNode = eventNode
      break
    case 'interrupt_required':
      status = 'interrupted'
      currentNode = eventNode
      break
    case 'run_resumed':
      status = 'resuming'
      currentNode = eventNode
      break
    case 'run_completed':
      status = 'completed'
      currentNode = null
      break
    case 'run_failed':
      status = 'failed'
      currentNode = null
      break
  }

  return {
    ...state,
    sessionId: event.session_id,
    threadId: event.thread_id,
    status,
    currentNode,
    completedNodes,
    lastEventId: event.event_id,
    events: [...state.events, event],
    errorMessage: null,
    activeEventSourceSession:
      event.event === 'run_completed' || event.event === 'run_failed'
        ? null
        : state.activeEventSourceSession,
  }
}

function parseEvent(data: string): AgentEvent {
  return JSON.parse(data) as AgentEvent
}

/**
 * 订阅某个 session 的 Agent 事件流。
 *
 * 为什么这样做：
 * - React 18 StrictMode 会在开发环境重复挂载 effect。
 * - 这里用 ref 持有当前 EventSource，并在重建前显式关闭，避免重复 SSE 连接。
 */
export function useAgentProgress(sessionId: string | null, threadId: string | null) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const eventSourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!sessionId || !threadId) {
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      dispatch({ type: 'reset' })
      return
    }

    eventSourceRef.current?.close()
    const eventSource = new EventSource(`/api/sessions/${sessionId}/events`)
    eventSourceRef.current = eventSource
    dispatch({ type: 'connect', sessionId, threadId })

    const cleanupFns = EVENT_NAMES.map((eventName) => {
      const handler = (messageEvent: MessageEvent<string>) => {
        const payload = parseEvent(messageEvent.data)
        dispatch({ type: 'event', payload })

        if (payload.thread_id === threadId && (payload.event === 'run_completed' || payload.event === 'run_failed')) {
          eventSource.close()
          if (eventSourceRef.current === eventSource) {
            eventSourceRef.current = null
          }
        }
      }

      eventSource.addEventListener(eventName, handler as EventListener)
      return () => eventSource.removeEventListener(eventName, handler as EventListener)
    })

    // 业务规则：EventSource 网络断开后会自动携带 Last-Event-ID 重连。
    // 仅当浏览器放弃重连（readyState === CLOSED）时才视为致命错误；
    // transient 错误（CONNECTING）不干预，避免阻断 SSE 断线续传。
    // 注意：EventSource API 不支持自定义 HTTP Header，X-Session-ID 通过
    // URL 路径 /api/sessions/{session_id}/events 传递，后端据此识别会话。
    eventSource.onerror = () => {
      if (eventSource.readyState === EventSource.CLOSED) {
        dispatch({ type: 'error', message: 'SSE 连接中断，请重新发起任务或刷新订阅。' })
        if (eventSourceRef.current === eventSource) {
          eventSourceRef.current = null
        }
      }
    }

    return () => {
      cleanupFns.forEach((cleanup) => cleanup())
      eventSource.close()
      if (eventSourceRef.current === eventSource) {
        eventSourceRef.current = null
      }
    }
  }, [sessionId, threadId])

  return useMemo(
    () => ({
      ...state,
      isConnected: Boolean(sessionId && threadId && state.activeEventSourceSession === sessionId),
    }),
    [sessionId, state, threadId],
  )
}