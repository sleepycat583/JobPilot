/** 模拟面试工作台：展示 Checkpoint 中的当前题、逐题反馈和面试复盘。 */
import { useEffect, useRef, useState } from 'react'
import { InterviewAnswerForm } from './InterviewAnswerForm'
import type { InterviewAnswerCommand, ThreadInterrupt } from '../types'
import type { InterviewState } from '../types'

function scoreLabel(key: string): string {
  return {
    technical_accuracy: '技术准确性',
    structure: '表达结构',
    job_relevance: '岗位相关性',
    evidence: '证据充分度',
  }[key] ?? key
}

/**
 * 将已持久化的面试状态映射为可恢复的阅读界面。
 *
 * 参数：
 * - interview: 后端 Checkpoint 返回的面试快照。
 * 返回：
 * - 当前题、已完成问题及结束后的结构化复盘视图。
 */
export function InterviewPanel({ interview, answerInterrupt, isResuming = false, onResume }: {
  interview: InterviewState
  answerInterrupt?: Extract<ThreadInterrupt, { type: 'interview_answer' }> | null
  isResuming?: boolean
  onResume?: (command: InterviewAnswerCommand) => void
}) {
  const [activeQuestionIndex, setActiveQuestionIndex] = useState(0)
  const [draftAnswers, setDraftAnswers] = useState<Record<string, string>>({})
  const lastCurrentQuestionId = useRef<string | null>(null)
  const completed = interview.question_records.filter((record) => Boolean(record.answer)).length
  const activeRecord = interview.question_records[activeQuestionIndex]
  const isCurrentAnswer = answerInterrupt?.question_id === activeRecord?.question_id

  useEffect(() => {
    if (lastCurrentQuestionId.current === interview.current_question_id) return
    lastCurrentQuestionId.current = interview.current_question_id
    const currentIndex = interview.question_records.findIndex((record) => record.question_id === interview.current_question_id)
    if (currentIndex >= 0) setActiveQuestionIndex(currentIndex)
  }, [interview.current_question_id, interview.question_records])

  useEffect(() => {
    setActiveQuestionIndex((index) => Math.min(index, Math.max(interview.question_records.length - 1, 0)))
    setDraftAnswers((drafts) => {
      const submittedIds = new Set(interview.question_records.filter((record) => record.answer).map((record) => record.question_id))
      const nextDrafts = Object.fromEntries(Object.entries(drafts).filter(([questionId]) => !submittedIds.has(questionId)))
      return Object.keys(nextDrafts).length === Object.keys(drafts).length ? drafts : nextDrafts
    })
  }, [interview.question_records])

  return <section className="interview-panel result-arrival" aria-label="模拟面试">
    <header>
      <div>
        <p className="eyebrow">模拟面试</p>
        <h2>{interview.status === 'completed' ? '面试复盘' : '正在进行'}</h2>
      </div>
      <strong>第 {activeRecord ? activeQuestionIndex + 1 : 0} / {interview.target_question_count} 题 · 已完成 {completed} 题</strong>
    </header>

    {activeRecord ? <article className="question-record" key={activeRecord.question_id}>
      <p className="question-topic">第 {activeQuestionIndex + 1} 题 · {activeRecord.topic}{activeRecord.follow_up_of ? ' · 追问' : ''}</p>
      <h3>{activeRecord.question}</h3>
      {activeRecord.answer ? <><p className="answer-copy">{activeRecord.answer}</p>
        {activeRecord.scores ? <dl className="interview-scores">{Object.entries(activeRecord.scores).map(([key, score]) => <div key={key}><dt>{scoreLabel(key)}</dt><dd>{score}</dd></div>)}</dl> : null}
        {activeRecord.feedback ? <p className="question-feedback">{activeRecord.feedback}</p> : null}
        {activeRecord.strengths.length ? <p><b>表现较好：</b>{activeRecord.strengths.join('；')}</p> : null}
        {activeRecord.issues.length ? <p><b>待改进：</b>{activeRecord.issues.join('；')}</p> : null}
      </> : isCurrentAnswer && onResume ? <InterviewAnswerForm interrupt={answerInterrupt} disabled={isResuming} onSubmit={onResume} answer={draftAnswers[activeRecord.question_id] ?? ''} onAnswerChange={(answer) => setDraftAnswers((drafts) => ({ ...drafts, [activeRecord.question_id]: answer }))} /> : <p className="waiting-answer">等待 AI 提供该题的回答入口</p>}
    </article> : <p className="waiting-answer">正在等待 AI 生成第一题</p>}

    <nav className="interview-navigation" aria-label="题目导航">
      <button type="button" className="secondary-button" disabled={activeQuestionIndex === 0} onClick={() => setActiveQuestionIndex((index) => index - 1)}>上一题</button>
      <span>{activeRecord ? `第 ${activeQuestionIndex + 1} 题` : '等待题目生成'}</span>
      <button type="button" className="secondary-button" disabled={!activeRecord || activeQuestionIndex >= interview.question_records.length - 1} onClick={() => setActiveQuestionIndex((index) => index + 1)}>下一题</button>
    </nav>

    {interview.report ? <section className="interview-report">
      <h3>复盘报告 <strong>{interview.report.overall_score}</strong></h3>
      <p>{interview.report.performance_summary}</p>
      {interview.report.recurring_strengths.length ? <p><b>重复优势：</b>{interview.report.recurring_strengths.join('；')}</p> : null}
      {interview.report.recurring_weaknesses.length ? <p><b>核心问题：</b>{interview.report.recurring_weaknesses.join('；')}</p> : null}
      {interview.report.review_actions.length ? <div className="review-actions"><h4>复习行动</h4>{interview.report.review_actions.map((action) => <article key={`${action.priority}-${action.weakness}`}><b>{action.priority} · {action.weakness}</b><p>知识点：{action.study_topic}</p><p>练习：{action.practice_action}</p><p>验收：{action.verification}</p></article>)}</div> : null}
    </section> : null}
  </section>
}