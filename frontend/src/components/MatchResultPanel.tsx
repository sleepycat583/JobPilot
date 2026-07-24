/**
 * 简历匹配结果面板。
 *
 * 展示后端已完成证据检索和确定性评分的结果；不在浏览器端推断分数或匹配结论。
 */
import type { MatchAnalysis, MatchEvidence, MatchResult } from '../types'

function EvidenceList({ evidence }: { evidence: MatchEvidence[] }) {
  if (evidence.length === 0) return <p className="match-empty-evidence">未检索到可引用的简历证据。</p>
  return <ul className="evidence-list">
    {evidence.map((item) => <li key={`${item.chunk_id}-${item.quote}`}>
      <q>{item.quote}</q><small>相关度 {Math.round(item.relevance * 100)}% · {item.chunk_id}</small>
    </li>)}
  </ul>
}

function ListSection({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return <section className="match-section"><h3>{title}</h3><ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section>
}

function AvailableMatchResult({ result }: { result: MatchResult }) {
  return <>
    <header>
      <p className="eyebrow">已基于选中简历完成匹配</p>
      <h2>简历匹配结果 <strong>{Math.round(result.total_score)} 分</strong></h2>
      <p>简历 ID：{result.resume_id}</p>
    </header>
    <section className="match-section"><h3>维度评分</h3><dl className="dimension-scores">
      {Object.entries(result.dimension_scores).map(([name, score]) => <div key={name}><dt>{name}</dt><dd>{Math.round(score)} 分</dd></div>)}
    </dl></section>
    <section className="match-section"><h3>逐项匹配</h3><div className="match-items">
      {result.matched_items.map((item) => <article key={item.requirement} className="match-item">
        <div><strong>{item.requirement}</strong><span className={`match-status ${item.status}`}>{item.status}</span><b>{Math.round(item.score)} 分</b></div>
        <p>{item.rationale}</p><EvidenceList evidence={item.evidence} />
      </article>)}
    </div></section>
    <ListSection title="匹配优势" items={result.strengths} />
    <ListSection title="主要缺口" items={result.gaps} />
    <ListSection title="改进建议" items={result.recommendations} />
  </>
}

/** 渲染可用匹配结论或不可用说明，避免把模型不可用误显示为零分。 */
export function MatchResultPanel({ result }: { result: MatchAnalysis }) {
  if ('status' in result) return <section className="match-result-panel" aria-label="匹配结果">
    <header><p className="eyebrow">暂无法生成匹配结论</p><h2>匹配结果不可用</h2><p>{result.message}</p></header>
    <section className="match-section"><h3>已检索到的简历证据</h3>
      {result.retrieval_evidence.map((item) => <article key={item.requirement} className="match-item"><strong>{item.requirement}</strong><EvidenceList evidence={item.evidence} /></article>)}
    </section>
  </section>

  return <section className="match-result-panel" aria-label="匹配结果"><AvailableMatchResult result={result} /></section>
}