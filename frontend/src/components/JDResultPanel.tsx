/** JD 解析结果面板：展示已由后端 Schema 校验并写入 Checkpoint 的结构化产物。 */
import { useEffect, useState } from 'react'
import type { JDParsed } from '../types'

function ListSection({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return <section className="jd-result-section">
    <h3>{title}</h3>
    <ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
  </section>
}

/**
 * 渲染已落盘的 JDParsed，不消费模型 token 或未校验的中间 JSON。
 *
 * 参数：
 * - result: 后端线程状态接口返回的完整 JDParsed。
 * 返回：
 * - 可在审核前显示的 JD 结构化分析结果。
 */
export function JDResultPanel({ result }: { result: JDParsed }) {
  const [visibleSections, setVisibleSections] = useState(1)

  useEffect(() => {
    setVisibleSections(1)
    const timers = [
      window.setTimeout(() => setVisibleSections(2), 160),
      window.setTimeout(() => setVisibleSections(3), 320),
      window.setTimeout(() => setVisibleSections(4), 480),
      window.setTimeout(() => setVisibleSections(5), 640),
    ]
    return () => timers.forEach(window.clearTimeout)
  }, [result])

  return <section className="jd-result-panel" aria-label="JD 解析结果">
    <header>
      <p className="eyebrow">已完成结构化提取</p>
      <h2>{result.job_title}</h2>
      <p>{result.seniority} · {result.company_name ?? '未识别公司'}</p>
    </header>
    {visibleSections >= 1 ? <section className="jd-result-section result-arrival">
      <h3>核心技能</h3>
      <ul className="skill-list">
        {result.skills.map((skill) => <li key={`${skill.name}-${skill.evidence}`}>
          <strong>{skill.name}</strong><span className={`priority ${skill.priority}`}>{skill.priority}</span>
          <small>{skill.evidence}</small>
        </li>)}
      </ul>
    </section> : null}
    {visibleSections >= 2 ? <div className="result-arrival"><ListSection title="岗位职责" items={result.responsibilities} /><ListSection title="经验要求" items={result.experience_requirements} /></div> : null}
    {visibleSections >= 3 ? <div className="result-arrival"><ListSection title="学历要求" items={result.education_requirements} /><ListSection title="面试重点" items={result.interview_focus} /></div> : null}
    {visibleSections >= 4 ? <div className="result-arrival"><ListSection title="待确认信息" items={result.ambiguities} /></div> : null}
  </section>
}