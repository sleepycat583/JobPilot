/** 简历匹配结果面板测试：正常结果与不可用结果必须被明确区分。 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MatchResultPanel } from './MatchResultPanel'

describe('MatchResultPanel', () => {
  it('renders score, matching evidence and recommendations', () => {
    render(<MatchResultPanel result={{
      total_score: 82, dimension_scores: { 技能: 88 },
      matched_items: [{ requirement: 'Python', status: 'matched', score: 90, rationale: '具备直接项目经验。', evidence: [{ chunk_id: 'resume:skills-001', quote: '熟悉 Python', relevance: 0.92 }] }],
      strengths: ['后端项目经验'], gaps: ['缺少 Kubernetes'], recommendations: ['补充云原生项目'], low_score_review_required: false, resume_id: 'resume-1',
    }} />)

    expect(screen.getByRole('heading', { name: /简历匹配结果/ })).toHaveTextContent('82 分')
    expect(screen.getByText('熟悉 Python')).toBeInTheDocument()
    expect(screen.getByText('补充云原生项目')).toBeInTheDocument()
  })

  it('does not render an unavailable match as a score', () => {
    render(<MatchResultPanel result={{ status: 'MATCH_UNAVAILABLE', resume_id: 'resume-1', message: '模型服务暂不可用', retrieval_evidence: [] }} />)
    expect(screen.getByRole('heading', { name: '匹配结果不可用' })).toBeInTheDocument()
    expect(screen.queryByText(/分$/)).not.toBeInTheDocument()
  })
})