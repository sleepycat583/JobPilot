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

  it('surfaces the error code when resume is rejected with 409', async () => {
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce('key-1').mockReturnValueOnce('key-2') })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ thread_id: 'thread-1', session_id: 'session-1', status: 'interrupted', review_status: 'in_review', review_target: 'jd_parsed', current_node: 'review', interrupt: firstInterrupt }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: async () => ({ error: { code: 'IDEMPOTENCY_KEY_REUSED', message: '幂等键已被使用，请刷新页面。' } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ thread_id: 'thread-1', session_id: 'session-1', status: 'interrupted', review_status: 'in_review', review_target: 'match_result', current_node: 'low_score_gate', interrupt: { type: 'low_match_score', target: 'match_result', score: 43.5, threshold: 60, top_gaps: [], accepted_actions: ['continue', 'cancel', 'revise_inputs'] } }),
      })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useThreadReview(null, null))
    await act(async () => { await result.current.loadState('thread-1') })
    expect(result.current.idempotencyKey).toBe('key-1')

    await act(async () => { await result.current.resume({ action: 'approve' }) })
    expect(result.current.error?.code).toBe('IDEMPOTENCY_KEY_REUSED')
    expect(result.current.error?.message).toContain('幂等键已被使用')
    expect(result.current.idempotencyKey).toBe('key-2')
    expect(result.current.state?.interrupt?.type).toBe('low_match_score')
  })

  it('uses the key paired with a newly loaded low-score interrupt', async () => {
    const lowScoreInterrupt = { type: 'low_match_score' as const, target: 'match_result' as const, score: 43.5, threshold: 60, top_gaps: [], accepted_actions: ['continue', 'cancel', 'revise_inputs'] }
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce('jd-key').mockReturnValueOnce('low-score-key') })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ thread_id: 'thread-1', session_id: 'session-1', status: 'interrupted', review_status: 'in_review', review_target: 'jd_parsed', current_node: 'review', interrupt: firstInterrupt }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ thread_id: 'thread-1', session_id: 'session-1', status: 'interrupted', review_status: 'in_review', review_target: 'match_result', current_node: 'low_score_gate', interrupt: lowScoreInterrupt }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ thread_id: 'thread-1', session_id: 'session-1', status: 'interrupted', review_status: 'in_review', review_target: 'match_result', current_node: 'review', interrupt: firstInterrupt }) })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useThreadReview(null, null))
    await act(async () => { await result.current.loadState('thread-1') })
    await act(async () => { await result.current.loadState('thread-1') })
    await act(async () => { await result.current.resume({ action: 'continue' }) })

    const request = JSON.parse(fetchMock.mock.calls[2][1].body)
    expect(request.idempotency_key).toBe('low-score-key')
    expect(request.command).toEqual({ action: 'continue' })
  })

  it('reports a fallback conflict message for 409 without a backend error body', async () => {
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValue('key-1') })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ thread_id: 'thread-1', session_id: 'session-1', status: 'interrupted', review_status: 'in_review', review_target: 'jd_parsed', current_node: 'review', interrupt: firstInterrupt }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: async () => ({}), // 无 error 字段
      })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useThreadReview(null, null))
    await act(async () => { await result.current.loadState('thread-1') })

    await act(async () => { await result.current.resume({ action: 'approve' }) })
    expect(result.current.error?.message).toContain('请求冲突')
  })
})