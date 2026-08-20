import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, BarChart3, Check, CheckCircle2, Clock3, FileText, LoaderCircle, RefreshCw, ShieldCheck, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspace } from '../../app/WorkspaceProvider'
import { api } from '../../lib/api/client'
import type { MatchReportHistory, MatchResult } from '../../shared/types'
import { AnalysisItem, EmptyState, Modal } from '../../shared/ui'

type Tab = 'overview' | 'strengths' | 'gaps' | 'evidence'
const totals: Record<string, number> = { '必备技能': 40, '核心职责': 30, '加分技能': 10, '硬性条件': 10, '证据质量': 10 }

export function MatchPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const workspace = useWorkspace()
  const { threadId, state, selectedResumeId, selectedJDId, setSelectedResumeId, setSelectedJDId, cancelActiveRun } = workspace
  const [strict, setStrict] = useState(false)
  const [tab, setTab] = useState<Tab>('overview')
  const resumes = useQuery({ queryKey: ['resumes'], queryFn: api.listResumes })
  const jds = useQuery({ queryKey: ['jds'], queryFn: api.listJDs })
  const history = useQuery({ queryKey: ['match-reports', threadId], queryFn: () => api.listMatchReports(threadId!), enabled: Boolean(threadId) })
  useEffect(() => { if (!selectedResumeId && resumes.data?.[0]) setSelectedResumeId(resumes.data[0].id) }, [resumes.data, selectedResumeId, setSelectedResumeId])
  useEffect(() => { if (!selectedJDId && jds.data?.[0]) setSelectedJDId(jds.data[0].id) }, [jds.data, selectedJDId, setSelectedJDId])

  const match = useMutation({
    mutationFn: () => api.createMatch({ thread_id: threadId!, resume_id: selectedResumeId, jd_id: selectedJDId, strict }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['thread-state', threadId] }); void queryClient.invalidateQueries({ queryKey: ['match-reports', threadId] }) },
  })
  const resume = useMutation({
    mutationFn: (action: string) => api.resumeThread(threadId!, { interrupt_id: state!.pending_interrupt!.id, action }),
    onSuccess: (next) => queryClient.setQueryData(['thread-state', threadId], next),
  })
  const cancelMatch = useMutation({
    mutationFn: cancelActiveRun,
    onSuccess: (next) => queryClient.setQueryData(['thread-state', threadId], next),
  })
  const result = state?.match_result
  const [historicalResult, setHistoricalResult] = useState<MatchReportHistory | null>(null)
  const lowInterrupt = state?.pending_interrupt?.type === 'low_match_score'
  const matchRunning = state?.status === 'running' || match.isPending
  const threadBusy = state?.status === 'running' || state?.status === 'interrupted' || match.isPending

  const displayedResult = historicalResult?.result ?? result
  return <div className="page-view match-view">
    <div className="match-config">
      <div className="selection-field"><label htmlFor="match-resume">简历版本</label><select id="match-resume" value={selectedResumeId} onChange={(event) => setSelectedResumeId(event.target.value)}><option value="">请选择简历</option>{resumes.data?.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></div>
      <div className="selection-field"><label htmlFor="match-jd">目标 JD</label><select id="match-jd" value={selectedJDId} onChange={(event) => setSelectedJDId(event.target.value)}><option value="">请选择 JD</option>{jds.data?.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></div>
      <label className="check-control"><input type="checkbox" checked={strict} onChange={(event) => setStrict(event.target.checked)} /><span><Check size={14} /></span>严格匹配</label>
      <button className="primary-button" type="button" onClick={() => { setHistoricalResult(null); match.mutate() }} disabled={threadBusy || !threadId || !selectedResumeId || !selectedJDId}>{matchRunning ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}{result ? '重新匹配' : '开始匹配'}</button>
      {matchRunning && <button className="text-button danger" type="button" onClick={() => cancelMatch.mutate()} disabled={cancelMatch.isPending}><XCircle size={16} />取消本次匹配</button>}
    </div>
    {match.error && <p className="inline-error">{match.error.message}</p>}
    {cancelMatch.error && <p className="inline-error">{cancelMatch.error.message}</p>}
    {history.data?.length ? <section className="history-list"><div className="history-list-heading"><span><Clock3 size={15} />已保存的匹配报告</span><small>{history.data.length} 条</small></div>{history.data.map((item) => <button className={historicalResult?.id === item.id ? 'active' : ''} key={item.id} type="button" onClick={() => setHistoricalResult(item)}><strong>{item.result.total_score} 分 · {item.strict ? '严格匹配' : '常规匹配'}</strong><span>{new Date(item.created_at).toLocaleString('zh-CN')}</span></button>)}</section> : null}
    {displayedResult ? <MatchResultView result={displayedResult} tab={tab} setTab={setTab} /> : <EmptyState title="等待匹配分析" detail="选择已解析的简历和 JD，系统会通过服务端任务生成可追溯的维度结果。" action={<div className="empty-actions"><button className="secondary-button" type="button" onClick={() => navigate('/resumes')}><FileText size={16} />管理简历</button><button className="secondary-button" type="button" onClick={() => navigate('/jd')}><BarChart3 size={16} />分析 JD</button></div>} />}
    {lowInterrupt && <Modal title="匹配分数较低" onClose={() => resume.mutate('cancel')}><div className="modal-alert"><AlertTriangle size={22} /><div><strong>当前匹配度为 {result?.total_score ?? 0} 分</strong><p>{state.pending_interrupt?.detail}</p></div></div><div className="modal-actions vertical"><button className="primary-button" type="button" onClick={() => resume.mutate('continue')} disabled={resume.isPending}>了解差距，继续准备</button><button className="secondary-button" type="button" onClick={() => resume.mutate('change_materials')}>更换简历或 JD</button><button className="text-button" type="button" onClick={() => resume.mutate('cancel')}>取消本次任务</button></div></Modal>}
  </div>
}

function MatchResultView({ result, tab, setTab }: { result: MatchResult; tab: Tab; setTab: (tab: Tab) => void }) {
  const low = result.total_score < 60
  return <><div className="score-band"><div className={`score-ring ${low ? 'low' : ''}`}><strong>{result.total_score}</strong><span>/ 100</span></div><div className="score-summary"><span className="eyebrow">综合匹配度</span><h2>{low ? '当前差距较明显' : '基本符合岗位要求'}</h2><p>{low ? '核心职责与部分必备技能缺少直接证据，建议先调整简历或目标岗位。' : '技术基础和项目方向匹配，建议继续补强主要差距中的项目证据。'}</p></div><div className="score-meta"><span><ShieldCheck size={16} />{result.evidence_count} 条证据</span><span><CheckCircle2 size={16} />AI 结构化评分</span></div></div><div className="tabs">{([['overview', '维度得分'], ['strengths', '匹配优势'], ['gaps', '主要差距'], ['evidence', '证据详情']] as const).map(([id, label]) => <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</div><MatchTab result={result} tab={tab} /></>
}

function MatchTab({ result, tab }: { result: MatchResult; tab: Tab }) {
  if (tab === 'strengths') return <div className="analysis-list">{result.strengths.map((title) => <AnalysisItem key={title} tone="positive" title={title} body="该优势由当前简历内容与岗位要求共同支持。" />)}</div>
  if (tab === 'gaps') return <div className="analysis-list">{result.gaps.map((title) => <AnalysisItem key={title} tone="warning" title={title} body="建议补充具体场景、个人行动和可验证结果。" />)}</div>
  if (tab === 'evidence') return <div className="evidence-table"><div className="evidence-row header"><span>岗位要求</span><span>简历证据</span><span>判定</span></div>{result.evidence?.length ? result.evidence.map((item, index) => <div className="evidence-row" key={`${item.requirement}-${index}`}><span>{item.requirement}</span><span>{item.resume_evidence || item.assessment || '未找到直接证据'}</span><strong className={item.status === 'matched' ? 'positive-text' : ''}>{item.status}</strong></div>) : <div className="evidence-row"><span>暂无证据</span><span>当前结果没有返回可展示的证据项</span><strong>待补充</strong></div>}</div>
  return <div className="dimension-list">{Object.entries(result.dimension_scores).map(([label, value]) => { const total = totals[label] ?? 100; return <div className="dimension" key={label}><div><strong>{label}</strong><span>{value} / {total}</span></div><div className="progress-track"><span style={{ width: `${value / total * 100}%` }} /></div></div> })}</div>
}
