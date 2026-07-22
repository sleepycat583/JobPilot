/** useAgentProgress SSE 事件状态机回归测试。 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAgentProgress } from './useAgentProgress'
import type { AgentEvent, AgentEventName } from '../types'

/** 构造一个最小合法的 SSE 事件 payload */
function makeEvent(overrides: Partial<AgentEvent> = {}): AgentEvent {
  return {
    event: 'node_started',
    event_id: 'evt-1',
    session_id: 'session-1',
    thread_id: 'thread-1',
    timestamp: new Date().toISOString(),
    node: 'parse_jd',
    node_kind: 'tool',
    node_run_id: 'run-1',
    started_at: null,
    ended_at: null,
    duration_ms: null,
    input_summary: '开始解析',
    error_code: null,
    success: null,
    ...overrides,
  }
}

/** 可控的 mock EventSource，模拟浏览器 SSE 行为 */
class MockEventSource {
  url: string
  readyState: 0 | 1 | 2 = 1 // OPEN
  onerror: (() => void) | null = null
  private listeners: Map<string, EventListener[]> = new Map()

  static readonly CONNECTING = 0 as const
  static readonly OPEN = 1 as const
  static readonly CLOSED = 2 as const

  constructor(url: string) {
    this.url = url
  }

  addEventListener(type: string, handler: EventListener) {
    const list = this.listeners.get(type) ?? []
    list.push(handler)
    this.listeners.set(type, list)
  }

  removeEventListener(type: string, handler: EventListener) {
    const list = this.listeners.get(type) ?? []
    this.listeners.set(
      type,
      list.filter((h) => h !== handler),
    )
  }

  close() {
    this.readyState = 2
    this.listeners.clear()
  }

  /** 测试辅助：模拟后端推送一个 SSE 事件 */
  emit(eventName: AgentEventName, data: AgentEvent) {
    const list = this.listeners.get(eventName) ?? []
    // MessageEvent 第二个参数是 MessageEventInit，jsdom 中 { data } 已足够
    list.forEach((h) => h(new MessageEvent(eventName, { data: JSON.stringify(data) })))
  }

  /** 测试辅助：模拟网络错误 */
  triggerError() {
    this.onerror?.()
  }
}

/** 包装 MockEventSource——每次构造时推入 instances 列表 */
const MockEventSourceCtor = class extends MockEventSource {
  constructor(url: string) {
    super(url)
    instances.push(this)
  }
} as unknown as {
  new (url: string): MockEventSource
  CONNECTING: number
  OPEN: number
  CLOSED: number
}
MockEventSourceCtor.CONNECTING = MockEventSource.CONNECTING
MockEventSourceCtor.OPEN = MockEventSource.OPEN
MockEventSourceCtor.CLOSED = MockEventSource.CLOSED

// 记录所有创建的 EventSource 实例，方便测试断言行
let instances: MockEventSource[] = []

afterEach(() => {
  vi.restoreAllMocks()
  instances = []
})

describe('useAgentProgress', () => {
  /** 标准 mock：用 MockEventSource 替换全局 EventSource */
  function setupMockEventSource() {
    vi.stubGlobal('EventSource', MockEventSourceCtor)
  }

  it('returns idle state when sessionId is null', () => {
    setupMockEventSource()
    const { result } = renderHook(() => useAgentProgress(null))
    expect(result.current.status).toBe('idle')
    expect(result.current.sessionId).toBeNull()
    expect(result.current.isConnected).toBe(false)
  })

  it('creates EventSource and connects when sessionId is provided', () => {
    setupMockEventSource()
    const { result } = renderHook(() => useAgentProgress('session-1'))
    expect(instances.length).toBe(1)
    expect(instances[0]!.url).toContain('/api/sessions/session-1/events')
    expect(result.current.status).toBe('running')
    expect(result.current.isConnected).toBe(true)
  })

  it('disconnects and resets when sessionId changes to null', () => {
    setupMockEventSource()
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useAgentProgress(id),
      { initialProps: { id: 'session-1' as string | null } },
    )
    expect(result.current.status).toBe('running')

    rerender({ id: null })
    expect(result.current.status).toBe('idle')
  })

  it.each([
    ['node_started', 'running'] as const,
    ['node_finished', 'running'] as const,
    ['node_retrying', 'running'] as const,
    ['interrupt_required', 'interrupted'] as const,
    ['run_resumed', 'resuming'] as const,
    ['run_completed', 'completed'] as const,
    ['run_failed', 'failed'] as const,
  ])('transitions to %s on %s event', (eventName, expectedStatus) => {
    setupMockEventSource()
    const { result } = renderHook(() => useAgentProgress('session-1'))

    act(() => {
      instances[0]!.emit(
        eventName,
        makeEvent({ event: eventName, node: 'test_node' }),
      )
    })

    expect(result.current.status).toBe(expectedStatus)
  })

  it('tracks completedNodes from node_finished events', () => {
    setupMockEventSource()
    const { result } = renderHook(() => useAgentProgress('session-1'))

    act(() => {
      instances[0]!.emit(
        'node_finished',
        makeEvent({ event: 'node_finished', node: 'parse_jd' }),
      )
    })
    act(() => {
      instances[0]!.emit(
        'node_finished',
        makeEvent({ event: 'node_finished', node: 'match_resume', event_id: 'evt-2' }),
      )
    })

    expect(result.current.completedNodes).toEqual(['parse_jd', 'match_resume'])
  })

  it('does not duplicate completedNodes for repeated node_finished', () => {
    setupMockEventSource()
    const { result } = renderHook(() => useAgentProgress('session-1'))

    act(() => {
      instances[0]!.emit(
        'node_finished',
        makeEvent({ event: 'node_finished', node: 'parse_jd' }),
      )
    })
    act(() => {
      instances[0]!.emit(
        'node_finished',
        makeEvent({ event: 'node_finished', node: 'parse_jd', event_id: 'evt-2' }),
      )
    })

    expect(result.current.completedNodes).toEqual(['parse_jd'])
  })

  it('updates lastEventId from each event', () => {
    setupMockEventSource()
    const { result } = renderHook(() => useAgentProgress('session-1'))

    act(() => {
      instances[0]!.emit(
        'node_started',
        makeEvent({ event: 'node_started', event_id: 'evt-5' }),
      )
    })

    expect(result.current.lastEventId).toBe('evt-5')
  })

  it('closes EventSource on run_completed', () => {
    setupMockEventSource()
    renderHook(() => useAgentProgress('session-1'))

    act(() => {
      instances[0]!.emit(
        'run_completed',
        makeEvent({ event: 'run_completed' }),
      )
    })

    expect(instances[0]!.readyState).toBe(MockEventSource.CLOSED)
  })

  it('closes EventSource on run_failed', () => {
    setupMockEventSource()
    renderHook(() => useAgentProgress('session-1'))

    act(() => {
      instances[0]!.emit(
        'run_failed',
        makeEvent({ event: 'run_failed' }),
      )
    })

    expect(instances[0]!.readyState).toBe(MockEventSource.CLOSED)
  })

  it('does not dispatch error on transient SSE error (readyState !== CLOSED)', () => {
    setupMockEventSource()
    const { result } = renderHook(() => useAgentProgress('session-1'))

    // readyState 默认为 OPEN (1)，模拟 transient 错误
    act(() => {
      instances[0]!.triggerError()
    })

    // 状态不应变为 failed，EventSource 不应被关闭
    expect(result.current.status).toBe('running')
    expect(instances[0]!.readyState).toBe(1) // still OPEN
  })

  it('dispatches fatal error when browser gives up (readyState === CLOSED)', () => {
    setupMockEventSource()
    const { result } = renderHook(() => useAgentProgress('session-1'))

    // 模拟浏览器放弃重连：先设 readyState 为 CLOSED，再触发 error
    instances[0]!.readyState = 2 // CLOSED

    act(() => {
      instances[0]!.triggerError()
    })

    expect(result.current.status).toBe('failed')
    expect(result.current.errorMessage).toContain('连接中断')
  })

  it('clears errorMessage when new events arrive after error', () => {
    setupMockEventSource()
    const { result } = renderHook(() => useAgentProgress('session-1'))

    // 先触发致命错误
    instances[0]!.readyState = 2
    act(() => {
      instances[0]!.triggerError()
    })
    expect(result.current.status).toBe('failed')

    // 重新挂载（模拟 sessionId 变化后新建连接）
    const { result: result2 } = renderHook(() => useAgentProgress('session-2'))
    expect(result2.current.errorMessage).toBeNull()
    expect(result2.current.status).toBe('running')
  })

  it('sets currentNode on node_started and clears on node_finished', () => {
    setupMockEventSource()
    const { result } = renderHook(() => useAgentProgress('session-1'))

    act(() => {
      instances[0]!.emit(
        'node_started',
        makeEvent({ event: 'node_started', node: 'decode' }),
      )
    })
    expect(result.current.currentNode).toBe('decode')

    act(() => {
      instances[0]!.emit(
        'node_finished',
        makeEvent({ event: 'node_finished', node: 'decode', event_id: 'evt-2' }),
      )
    })
    expect(result.current.currentNode).toBeNull()
  })
})
