/** 模拟面试工作台：展示 Checkpoint 中的当前题、逐题反馈和面试复盘。 */
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
export function InterviewPanel({ interview }: { interview: InterviewState }) {
  const completed = interview.question_records.filter((record) => Boolean(record.answer)).length
  return <section className="interview-panel result-arrival" aria-label="模拟面试">
    <header>
      <div>
        <p className="eyebrow">模拟面试</p>
        <h2>{interview.status === 'completed' ? '面试复盘' : '正在进行'}</h2>
      </div>
      <strong>{completed} / {interview.target_question_count} 题</strong>
    </header>

    {interview.question_records.map((record, index) => <article className="question-record" key={record.question_id}>
      <p className="question-topic">第 {index + 1} 题 · {record.topic}{record.follow_up_of ? ' · 追问' : ''}</p>
      <h3>{record.question}</h3>
      {record.answer ? <><p className="answer-copy">{record.answer}</p>
        {record.scores ? <dl className="interview-scores">{Object.entries(record.scores).map(([key, score]) => <div key={key}><dt>{scoreLabel(key)}</dt><dd>{score}</dd></div>)}</dl> : null}
        {record.feedback ? <p className="question-feedback">{record.feedback}</p> : null}
        {record.strengths.length ? <p><b>表现较好：</b>{record.strengths.join('；')}</p> : null}
        {record.issues.length ? <p><b>待改进：</b>{record.issues.join('；')}</p> : null}
      </> : <p className="waiting-answer">等待你的回答</p>}
    </article>)}

    {interview.report ? <section className="interview-report">
      <h3>复盘报告 <strong>{interview.report.overall_score}</strong></h3>
      <p>{interview.report.performance_summary}</p>
      {interview.report.recurring_strengths.length ? <p><b>重复优势：</b>{interview.report.recurring_strengths.join('；')}</p> : null}
      {interview.report.recurring_weaknesses.length ? <p><b>核心问题：</b>{interview.report.recurring_weaknesses.join('；')}</p> : null}
      {interview.report.review_actions.length ? <div className="review-actions"><h4>复习行动</h4>{interview.report.review_actions.map((action) => <article key={`${action.priority}-${action.weakness}`}><b>{action.priority} · {action.weakness}</b><p>知识点：{action.study_topic}</p><p>练习：{action.practice_action}</p><p>验收：{action.verification}</p></article>)}</div> : null}
    </section> : null}
  </section>
}