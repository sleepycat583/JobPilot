import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Circle, Clock3, FileText, LoaderCircle, Mic2, RefreshCw, Send } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useWorkspace } from '../../app/WorkspaceProvider'
import { api } from '../../lib/api/client'
import type { InterviewHistory, InterviewState } from '../../shared/types'
import { SectionTitle } from '../../shared/ui'

export function InterviewPage() {
  const queryClient = useQueryClient()
  const { threadId, state, selectedResumeId, selectedJDId, setSelectedResumeId, setSelectedJDId } = useWorkspace()
  const resumes = useQuery({ queryKey: ['resumes'], queryFn: api.listResumes })
  const jds = useQuery({ queryKey: ['jds'], queryFn: api.listJDs })
  const history = useQuery({ queryKey: ['interview-history', threadId], queryFn: () => api.listInterviewHistory(threadId!), enabled: Boolean(threadId) })
  const [interviewType, setInterviewType] = useState<'综合面试' | '技术专项' | '项目深挖'>('综合面试')
  const [feedbackMode, setFeedbackMode] = useState<'each' | 'final'>('each')
  const [questionCount, setQuestionCount] = useState(5)
  const [answer, setAnswer] = useState('')
  const [setupOverride, setSetupOverride] = useState(false)
  useEffect(() => { if (!selectedResumeId && resumes.data?.[0]) setSelectedResumeId(resumes.data[0].id) }, [resumes.data, selectedResumeId, setSelectedResumeId])
  useEffect(() => { if (!selectedJDId && jds.data?.[0]) setSelectedJDId(jds.data[0].id) }, [jds.data, selectedJDId, setSelectedJDId])

  const start = useMutation({
    mutationFn: () => api.startInterview({ thread_id: threadId!, resume_id: selectedResumeId, jd_id: selectedJDId, interview_type: interviewType, question_count: questionCount, feedback_mode: feedbackMode }),
    onSuccess: () => { setSetupOverride(false); void queryClient.invalidateQueries({ queryKey: ['thread-state', threadId] }); void queryClient.invalidateQueries({ queryKey: ['interview-history', threadId] }) },
  })
  const resume = useMutation({
    mutationFn: ({ action, payload }: { action: string; payload?: Record<string, unknown> }) => api.resumeThread(threadId!, { interrupt_id: state!.pending_interrupt!.id, action, payload }),
    onSuccess: (next) => {
      setAnswer('')
      queryClient.setQueryData(['thread-state', threadId], next)
      void queryClient.invalidateQueries({ queryKey: ['interview-history', threadId] })
    },
  })
  const interview = state?.interview
  const [historyItem, setHistoryItem] = useState<InterviewHistory | null>(null)

  if (historyItem) return <InterviewReport interview={historyItem.result} onRestart={() => setHistoryItem(null)} historyItem={historyItem} />
  if (!interview || setupOverride) return <><InterviewSetup
    interviewType={interviewType} setInterviewType={setInterviewType}
    feedbackMode={feedbackMode} setFeedbackMode={setFeedbackMode}
    questionCount={questionCount} setQuestionCount={setQuestionCount}
    resumeId={selectedResumeId} setResumeId={setSelectedResumeId} resumes={resumes.data ?? []}
    jdId={selectedJDId} setJDId={setSelectedJDId} jds={jds.data ?? []}
    onStart={() => start.mutate()} pending={start.isPending} error={start.error?.message}
  />{history.data?.length ? <InterviewHistoryList items={history.data} onSelect={setHistoryItem} /> : null}</>
  if (interview.phase === 'report' && interview.report) return <><InterviewReport interview={interview} onRestart={() => setSetupOverride(true)} />{history.data?.length ? <InterviewHistoryList items={history.data} onSelect={setHistoryItem} /> : null}</>
  return <div className="page-view interview-session">
    <div className="interview-progress"><div><span>{interview.interview_type}</span><strong>第 {interview.current_index + 1} / {interview.question_count} 题</strong></div><div className="progress-track"><span style={{ width: `${(interview.current_index + 1) / interview.question_count * 100}%` }} /></div><button className="text-button danger" type="button" onClick={() => resume.mutate({ action: 'end' })}>结束面试</button></div>
    <div className="question-stage"><div className="question-topic">{interview.current_index < 2 ? '项目经历' : '技术能力'}</div><h2>{interview.current_question}</h2>{interview.phase === 'feedback' && interview.feedback ? <div className="feedback-panel"><div className="feedback-score"><strong>{interview.feedback.score}</strong><span>本题表现</span></div><div><h3>{interview.feedback.title}</h3><p>{interview.feedback.detail}</p><div className="tag-list">{interview.feedback.tags.map((tag, index) => <span key={tag} className={`tag ${index === 0 ? 'required' : index === 1 ? 'preferred' : 'inferred'}`}>{tag}</span>)}</div></div><button className="primary-button" type="button" onClick={() => resume.mutate({ action: 'next' })} disabled={resume.isPending}>下一题</button></div> : <div className="answer-area"><textarea value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="像真实面试一样作答。建议说明背景、行动、结果和你的判断依据。" autoFocus /><div className="answer-footer"><span>{answer.length} 字</span><div><button className="secondary-button" type="button" onClick={() => resume.mutate({ action: 'skip' })}>暂时跳过</button><button className="primary-button" type="button" disabled={!answer.trim() || resume.isPending} onClick={() => resume.mutate({ action: 'submit_answer', payload: { answer } })}>{resume.isPending ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />}提交回答</button></div></div></div>}</div>
    <InterviewOutline interview={interview} />
  </div>
}

type SetupProps = {
  interviewType: '综合面试' | '技术专项' | '项目深挖'; setInterviewType: (value: '综合面试' | '技术专项' | '项目深挖') => void
  feedbackMode: 'each' | 'final'; setFeedbackMode: (value: 'each' | 'final') => void
  questionCount: number; setQuestionCount: (value: number) => void
  resumeId: string; setResumeId: (value: string) => void; resumes: Array<{ id: string; display_name: string }>
  jdId: string; setJDId: (value: string) => void; jds: Array<{ id: string; title: string }>
  onStart: () => void; pending: boolean; error?: string
}

function InterviewSetup(props: SetupProps) {
  return <div className="page-view interview-view setup-view"><div className="setup-heading"><span className="eyebrow">新建模拟面试</span><h2>按目标岗位练习真实问题</h2></div><div className="setup-form">
    <div className="form-row"><label htmlFor="interview-jd">目标岗位</label><select id="interview-jd" className="select-button" value={props.jdId} onChange={(event) => props.setJDId(event.target.value)}><option value="">请选择 JD</option>{props.jds.map((jd) => <option key={jd.id} value={jd.id}>{jd.title}</option>)}</select></div>
    <div className="form-row"><label htmlFor="interview-resume">使用简历</label><select id="interview-resume" className="select-button" value={props.resumeId} onChange={(event) => props.setResumeId(event.target.value)}><option value="">请选择简历</option>{props.resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.display_name}</option>)}</select></div>
    <div className="form-row"><label>面试类型</label><div className="segmented-control">{(['综合面试', '技术专项', '项目深挖'] as const).map((type) => <button key={type} className={props.interviewType === type ? 'active' : ''} type="button" aria-pressed={props.interviewType === type} onClick={() => props.setInterviewType(type)}>{type}</button>)}</div></div>
    <div className="form-row"><label>题目数量</label><div className="stepper"><button type="button" aria-label="减少题数" disabled={props.questionCount <= 3} onClick={() => props.setQuestionCount(Math.max(3, props.questionCount - 1))}>-</button><strong>{props.questionCount}</strong><button type="button" aria-label="增加题数" disabled={props.questionCount >= 5} onClick={() => props.setQuestionCount(Math.min(5, props.questionCount + 1))}>+</button></div></div>
    <div className="form-row"><label>反馈方式</label><div className="segmented-control"><button className={props.feedbackMode === 'each' ? 'active' : ''} type="button" onClick={() => props.setFeedbackMode('each')}>逐题反馈</button><button className={props.feedbackMode === 'final' ? 'active' : ''} type="button" onClick={() => props.setFeedbackMode('final')}>结束后复盘</button></div></div>
    {props.error && <p className="inline-error">{props.error}</p>}<button className="primary-button start-interview" type="button" onClick={props.onStart} disabled={!props.resumeId || !props.jdId || props.pending}>{props.pending ? <LoaderCircle className="spin" size={18} /> : <Mic2 size={18} />}开始面试</button>
  </div></div>
}

function InterviewOutline({ interview }: { interview: InterviewState }) {
  const topics = ['项目经历', '性能优化', '消息可靠性', '缓存设计', '系统复盘'].slice(0, interview.question_count)
  return <aside className="interview-outline"><span className="section-label">面试提纲</span>{topics.map((topic, index) => <div key={topic} className={index === interview.current_index ? 'active' : index < interview.current_index ? 'done' : ''}>{index < interview.current_index ? <CheckCircle2 size={16} /> : index === interview.current_index ? <LoaderCircle size={16} /> : <Circle size={16} />}<span>{topic}</span></div>)}</aside>
}

const reportDimensionLabels: Record<string, string> = {
  technical_depth: '技术深度',
  evidence_specificity: '证据充分度',
  evidence_quality: '证据充分度',
  jd_alignment: '岗位匹配度',
  job_alignment: '岗位匹配度',
  communication_clarity: '表达清晰度',
  communication: '表达清晰度',
}

function InterviewHistoryList({ items, onSelect }: { items: InterviewHistory[]; onSelect: (item: InterviewHistory) => void }) {
  return <section className="interview-history"><div className="history-list-heading"><span><Clock3 size={15} />已保存的面试复盘</span><small>{items.length} 条</small></div>{items.map((item) => <button key={item.id} type="button" onClick={() => onSelect(item)}><strong>{item.interview_type} · {item.overall_score ?? '--'} 分</strong><span>{new Date(item.completed_at).toLocaleString('zh-CN')} · 完成 {item.result.records.length} 题</span></button>)}</section>
}

function InterviewReport({ interview, onRestart, historyItem }: { interview: InterviewState; onRestart: () => void; historyItem?: InterviewHistory }) {
  const report = interview.report!
  return <div className="page-view report-view"><div className="report-header"><div><span className="eyebrow">{historyItem ? '历史模拟面试复盘' : '模拟面试复盘'}</span><h2>{historyItem ? '已保存的面试复盘' : '本轮面试复盘'}</h2><p className="report-summary">{report.summary || '复盘已生成，请结合具体问题回看自己的回答。'}</p><p className="report-meta">完成 {interview.records.length} 道问题 · {interview.interview_type}</p></div><div className="report-score"><strong>{report.overall_score}</strong><span>/ 100</span></div></div><div className="report-grid"><section><SectionTitle title="维度表现" meta={`${Object.keys(report.dimension_scores).length} 项`} />{Object.entries(report.dimension_scores).map(([name, score]) => <div className="dimension compact" key={name}><div><strong>{reportDimensionLabels[name] ?? name}</strong><span>{score}</span></div><div className="progress-track"><span style={{ width: `${score}%` }} /></div></div>)}</section><section><SectionTitle title="优先改进" meta="行动项" />{report.actions.map((action) => <div className="action-item" key={action}><strong>{action}</strong><p>根据本轮回答补充可验证的项目证据与技术判断。</p></div>)}</section></div><div className="report-actions"><button className="secondary-button" type="button" onClick={onRestart}><RefreshCw size={16} />{historyItem ? '返回当前面试' : '再练一轮'}</button></div></div>
}
