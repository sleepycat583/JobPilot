/** useThreadReview 的刷新恢复与幂等键回归测试。 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useThreadReview } from './useThreadReview'

const firstInterrupt = {
  type: 'final_review' as const,
  target: 'jd_parsed',
  accepted_actions: ['approve', 'reject'],
  draft: {},
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useThreadReview', () => {
  it('keeps the key for the same interrupt and rotates it for a new interrupt', async () => {
    const fetchMock = vi.fn()
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => ({ thread_id: 'thread-1', session_id: 'session-1', status: 'interrupted', review_status: 'in_review', review_target: 'jd_parsed', current_node: 'review', interrupt: firstInterrupt }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ thread_id: 'thread-1', session_id: 'session-1', status: 'interrupted', review_status: 'in_review', review_target: 'jd_parsed', current_node: 'review', interrupt: firstInterrupt }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ thread_id: 'thread-1', session_id: 'session-1', status: 'interrupted', review_status: 'in_review', review_target: 'interview_state', current_node: 'answer', interrupt: { type: 'interview_answer', target: 'interview_state', question_id: 'q-1', question: '问题', accepted_actions: ['submit_answer'] } }) })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce('key-1').mockReturnValueOnce('key-2') })

    const { result } = renderHook(() => useThreadReview(null, null))
    await act(async () => { await result.current.loadState('thread-1') })
    const firstKey = result.current.idempotencyKey
    await act(async () => { await result.current.loadState('thread-1') })
    expect(result.current.idempotencyKey).toBe(firstKey)

    await act(async () => { await result.current.loadState('thread-1') })
    expect(result.current.idempotencyKey).toBe('key-2')
  })

  it('clears the active interrupt after a checkpoint-not-found response', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: { code: 'CHECKPOINT_NOT_FOUND', message: '不存在' } }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useThreadReview(null, null))
    await act(async () => { await expect(result.current.loadState('thread-1')).rejects.toMatchObject({ code: 'CHECKPOINT_NOT_FOUND' }) })
    expect(result.current.state).toBeNull()
    expect(result.current.idempotencyKey).toBeNull()
  })
})