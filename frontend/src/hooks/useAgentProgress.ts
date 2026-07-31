import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'

import type { AgentEvent, AgentEventName, AgentProgressState } from '../types'

type ProgressAction =
  | { type: 'connect'; sessionId: string; threadId: string }
  | { type: 'event'; payload: AgentEvent }
  | { type: 'checkpoint'; status: AgentProgressState['status']; currentNode: string | null }
  | { type: 'error'; message: string }
  | { type: 'reset' }

export type NodeProgressStatus = 'pending' | 'running' | 'completed' | 'interrupted' | 'failed'

/** JD 与简历匹配组合任务的稳定控制流顺序；UI 不再依赖事件到达数量决定列表长度。 */
export const JD_PROGRESS_NODES = [
  'rolling_summary',
  'supervisor',
  'queue_dispatch',
  'jd_parser',
  'prepare_final_review',
  'final_review_gate',
  'resume_matcher',
  'prepare_low_score_review',
  'low_score_gate',
  'interview_plan',
  'ask_question',
  'interview_await_answer',
  'evaluate_answer',
  'interview_decision',
  'generate_review_report',
  'finalize_node',
  'api',
] as const

type NodeProgressMap = Record<string, NodeProgressStatus>

const NODE_INDEX = new Map<string, number>(JD_PROGRESS_NODES.map((node, index) => [node, index]))

function emptyNodeProgress(): NodeProgressMap {
  return Object.fromEntries(JD_PROGRESS_NODES.map((node) => [node, 'pending']))
}

/**
 * 将流程推进到指定节点，并保持已经完成的节点不回退。
 *
 * 参数 node 是后端当前正在处理或刚处理完的固定流程节点；返回值是新的
 * 节点状态投影。这里按固定流程补齐前置节点，是为了在 SSE 事件丢失、或
 * Checkpoint 比事件更晚到达时，右侧状态仍然只会向前推进。
 */
function advanceNodeProgress(
  current: NodeProgressMap,
  node: string | null,
  nodeStatus: NodeProgressStatus,
): NodeProgressMap {
  if (!node) return current
  const targetIndex = NODE_INDEX.get(node)
  if (targetIndex === undefined) return current

  const next = { ...current }
  for (let index = 0; index < targetIndex; index += 1) {
    const previousNode = JD_PROGRESS_NODES[index]
    if (next[previousNode] !== 'failed') next[previousNode] = 'completed'
  }

  const existing = next[node]
  if (existing === 'completed' && nodeStatus !== 'failed') return next
  next[node] = nodeStatus
  return next
}

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
  nodeProgress: emptyNodeProgress(),
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
      nodeProgress: emptyNodeProgress(),
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

  if (action.type === 'checkpoint') {
    const nodeProgress = advanceNodeProgress(
      state.nodeProgress,
      action.currentNode,
      action.status === 'interrupted' ? 'interrupted' : action.status === 'failed' ? 'failed' : 'running',
    )
    if (action.status === 'completed') Object.keys(nodeProgress).forEach((node) => { nodeProgress[node] = 'completed' })
    return { ...state, status: action.status, currentNode: action.currentNode, nodeProgress }
  }

  const event = action.payload
  // SSE 是 session 级通道。当前页面只展示本次提交的 thread，回放或并行任务事件
  // 不能改变该任务的进度状态。
  if (event.thread_id !== state.threadId || state.events.some((item) => item.event_id === event.event_id)) {
    return state
  }
  const completedNodes = [...state.completedNodes]
  const eventNode = event.node ?? null
  const nodeProgress = { ...state.nodeProgress }

  if (event.event === 'node_finished' && eventNode && !completedNodes.includes(eventNode)) {
    completedNodes.push(eventNode)
  }
  if (eventNode && nodeProgress[eventNode] !== undefined) {
    if (event.event === 'node_started' || event.event === 'node_retrying' || event.event === 'run_resumed') {
      Object.assign(nodeProgress, advanceNodeProgress(nodeProgress, eventNode, 'running'))
    }
    if (event.event === 'node_finished') {
      Object.assign(nodeProgress, advanceNodeProgress(nodeProgress, eventNode, 'completed'))
    }
    if (event.event === 'interrupt_required') {
      Object.assign(nodeProgress, advanceNodeProgress(nodeProgress, eventNode, 'interrupted'))
    }
    if (event.event === 'run_failed') {
      Object.assign(nodeProgress, advanceNodeProgress(nodeProgress, eventNode, 'failed'))
    }
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
      // 业务规则：后端发布 run_completed 代表本次任务已收敛，固定流程投影中的步骤全部完成。
      Object.keys(nodeProgress).forEach((node) => { nodeProgress[node] = 'completed' })
      break
    case 'run_failed':
      status = 'failed'
      currentNode = null
      if (eventNode && nodeProgress[eventNode] !== undefined) nodeProgress[eventNode] = 'failed'
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
    errorMessage: event.event === 'run_failed' ? event.detail ?? '任务执行失败，请检查模型服务连接后重试。' : null,
    nodeProgress,
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

  const syncCheckpointState = useCallback((status: AgentProgressState['status'], currentNode: string | null) => {
    dispatch({ type: 'checkpoint', status, currentNode })
  }, [])

  // 参数切换到新线程后，effect 尚未执行前仍可能保留上一线程的 reducer 快照。
  // 这里先暴露新线程的临时运行状态，避免旧任务的 completed 污染新任务 UI。
  const visibleState = state.threadId === threadId && state.sessionId === sessionId
    ? state
    : {
        ...initialState,
        sessionId,
        threadId,
        status: sessionId && threadId ? 'running' as const : 'idle' as const,
        activeEventSourceSession: sessionId,
      }

  return useMemo(
    () => ({
      ...visibleState,
      syncCheckpointState,
      isConnected: Boolean(sessionId && threadId && state.activeEventSourceSession === sessionId),
    }),
    [sessionId, state, syncCheckpointState, threadId, visibleState],
  )
}