/** 求职分析工作台入口。 */
import { useState } from 'react'

import type { ApiErrorResponse, TaskAcceptedResponse } from './types'

// TODO(Step 11): 删除本地 Resume/JDParsed/MatchResult/Analysis 缩减类型
type Resume = { resume_version: string; file_name: string; index_status: 'indexed' | 'failed' }
type JDParsed = { job_title: string; responsibilities: string[]; skills: { name: string; evidence: string }[]; experience_requirements: string[] }
type MatchResult = { total_score: number; strengths: string[]; gaps: string[]; recommendations: string[]; resume_version: string }
type Analysis = { jd_parsed: JDParsed; match_result: MatchResult }
type State = 'review' | 'loading' | 'completed' | 'error' | 'empty'

const resumes: Resume[] = [
  { resume_version: 'v3', file_name: 'resume_v3.pdf', index_status: 'indexed' },
  { resume_version: 'v2', file_name: 'resume_v2.pdf', index_status: 'indexed' },
  { resume_version: 'draft', file_name: 'resume_draft.docx', index_status: 'failed' },
]
const initialJD = '负责 AI Agent 相关产品的后端开发，要求熟悉 LangGraph/LangChain，具备 RAG 系统搭建经验...'
const defaultResult: Analysis = { jd_parsed: { job_title: 'AI Agent 后端工程师', responsibilities: ['AI Agent 产品后端开发', 'RAG 系统搭建'], skills: [{ name: 'LangGraph、LangChain、RAG、Python', evidence: '原文第 2 段要求熟悉 LangGraph/LangChain' }], experience_requirements: ['未在原文中明确提及，标记为待澄清'] }, match_result: { total_score: 82, strengths: ['具备 Python 后端开发经验'], gaps: ['LangGraph 项目证据不足'], recommendations: ['补充 LangGraph 编排与中断恢复的项目描述'], resume_version: 'v3' } }

/** X-Session-ID 持久化 key（第 0 章全局约束） */
const SESSION_STORAGE_KEY = 'job-assistant.x-session-id'

/** 从 sessionStorage 恢复已持久化的 X-Session-ID */
function loadSessionId(): string | null {
  return sessionStorage.getItem(SESSION_STORAGE_KEY)
}

/** 将后端返回的 session_id 持久化，后续所有请求携带此值 */
function saveSessionId(id: string) {
  sessionStorage.setItem(SESSION_STORAGE_KEY, id)
}

/**
 * 解析后端统一错误响应 { error: { code, message } }。
 * 约定：所有 /api/* 和 /v1/* 接口失败时返回此格式（第 0 章）。
 */
function parseApiError(response: Response, body: unknown, fallback: string): Error & { code?: string } {
  const apiError = (body as ApiErrorResponse | null)?.error
  const message = apiError?.message ?? `${fallback} (HTTP ${response.status})`
  return Object.assign(new Error(message), { code: apiError?.code })
}

/** 模拟 JD 分析，字段与后端 JDParsed、MatchResult Schema 对齐。 */
function mockAnalyzeJob(jd_text: string, resume_version: string): Promise<Analysis> {
  // TODO: 对接 POST /api/tasks、GET /api/sessions/{session_id}/events 与线程状态接口。
  return new Promise((resolve, reject) => window.setTimeout(() => {
    if (jd_text.includes('[error]')) return reject(new Error('模拟解析失败，请修改 JD 后重试。'))
    if (!jd_text.trim()) return reject(new Error('请先填写岗位描述。'))
    resolve({ ...defaultResult, match_result: { ...defaultResult.match_result, resume_version } })
  }, 850))
}

function App() {
  const [jdText, setJdText] = useState(initialJD)
  const [resumeVersion, setResumeVersion] = useState('v3')
  const [tab, setTab] = useState<'jd' | 'match'>('jd')
  const [state, setState] = useState<State>('review')
  const [result, setResult] = useState<Analysis | null>(defaultResult)
  const [error, setError] = useState<string | null>(null)
  // 任务/线程状态（Step 7: POST /api/tasks 后由 202 响应填充）
  const [sessionId, setSessionId] = useState<string | null>(loadSessionId())
  const [threadId, setThreadId] = useState<string | null>(null)
  const fileName = resumes.find((item) => item.resume_version === resumeVersion)?.file_name ?? '未选择'

  /**
   * 启动分析：POST /api/tasks → 保存 session_id/thread_id。
   *
   * 业务规则（第 0 章）：
   * - X-Session-ID 首次省略 → 后端生成 → 前端持久化到 sessionStorage
   * - 后续请求复用持久化的 session_id
   * - 错误按 { error: { code, message } } 解析
   *
   * FIXME: 后端未就绪时降级为 mockAnalyzeJob，Step 11 删除。
   */
  async function analyze() {
    setState('loading'); setError(null); setResult(null)
    try {
      const xSessionId = loadSessionId()
      const requestBody: Record<string, unknown> = { jd_text: jdText }
      if (resumeVersion) requestBody.resume_version = resumeVersion
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (xSessionId) headers['X-Session-ID'] = xSessionId

      const response = await fetch('/api/tasks', { method: 'POST', headers, body: JSON.stringify(requestBody) })

      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw parseApiError(response, body, '任务提交失败')
      }

      const data = (await response.json()) as TaskAcceptedResponse
      saveSessionId(data.session_id)
      setSessionId(data.session_id)
      setThreadId(data.thread_id)
      // 202 accepted — 进度由 useAgentProgress 通过 SSE 驱动（Step 8 接入）
      setState('review') // 保留审核 UI，后续 Step 9-10 切换为真实线程审核
      setTab('jd')
    } catch (cause: unknown) {
      if (cause instanceof TypeError && cause.message.includes('fetch')) {
        // 网络不可用时降级为 mock
        try {
          setResult(await mockAnalyzeJob(jdText, resumeVersion)); setTab('jd'); setState('review')
        } catch (mockCause) {
          setError(mockCause instanceof Error ? mockCause.message : '分析失败，请重试。'); setState('error')
        }
      } else {
        setError(cause instanceof Error ? cause.message : '分析失败，请重试。'); setState('error')
      }
    }
  }
  return <main className="workbench">
    <aside className="resume-sidebar" aria-label="简历库"><h1>简历库</h1><div className="resume-list">{resumes.map((resume) => <button key={resume.resume_version} type="button" className={`resume-item ${resumeVersion === resume.resume_version ? 'selected' : ''}`} onClick={() => setResumeVersion(resume.resume_version)}><strong>{resume.file_name}</strong><span className={`resume-status ${resume.index_status}`}><i />{resume.index_status === 'indexed' ? `已索引 · ${resume.resume_version}` : '索引失败'}</span></button>)}</div><button type="button" className="upload-button" onClick={() => setState('empty')}><span>＋</span>上传新版本简历</button></aside>
    <section className="analysis-area"><label htmlFor="jd-input" className="input-label">粘贴职位描述（JD）</label><textarea id="jd-input" className="jd-input" value={jdText} onChange={(event) => setJdText(event.target.value)} aria-label="职位描述" /><div className="analysis-action-row"><p>将匹配: <b>{fileName}</b><span>（不选则仅做 JD 解析）</span></p><button type="button" className="primary-button" onClick={() => void analyze()} disabled={state === 'loading'}>{state === 'loading' ? '分析中...' : '开始分析'}</button></div>
      <div className="tabs" role="tablist" aria-label="分析结果"><button type="button" role="tab" aria-selected={tab === 'jd'} className={tab === 'jd' ? 'active' : ''} onClick={() => setTab('jd')}>JD 解析</button><button type="button" role="tab" aria-selected={tab === 'match'} className={tab === 'match' ? 'active' : ''} onClick={() => setTab('match')}>匹配结果</button></div>
      {state === 'loading' && <div className="state-card loading-state"><span className="spinner" />正在解析 JD 与检索简历证据...</div>}{state === 'error' && <div className="state-card error-state"><p>{error}</p><button type="button" className="secondary-button" onClick={() => void analyze()}>重试</button></div>}{state === 'empty' && <div className="state-card empty-state">请上传并完成索引后，再选择简历进行匹配。</div>}
      {result && state !== 'loading' && state !== 'error' && state !== 'empty' && (tab === 'jd' ? <JDResult data={result.jd_parsed} /> : <MatchResultView data={result.match_result} />)}</section>
    <aside className="progress-sidebar" aria-label="执行进度"><h2>执行进度</h2><ol className="progress-list"><li className="done"><span>✓</span>正在解析 JD</li><li className={state === 'review' ? 'current' : state === 'completed' ? 'done' : ''}><span>{state === 'completed' ? '✓' : ''}</span>等待审核</li><li className={state === 'completed' ? 'done' : ''}><span>{state === 'completed' ? '✓' : ''}</span>检索简历证据</li><li className={state === 'completed' ? 'done' : ''}><span>{state === 'completed' ? '✓' : ''}</span>生成匹配结果</li></ol>
      {state === 'review' && <section className="review-notice"><strong><i />待人工审核 · JD解析</strong><p>JD 解析结果已生成，请核对左侧岗位信息是否准确后再继续匹配。</p><div><button type="button" className="primary-button" onClick={() => setState('completed')}>批准</button><button type="button" className="secondary-button" onClick={() => void analyze()}>驳回并重试</button></div><small>驳回会重新生成解析结果，暂不支持按具体意见修改</small></section>}{state === 'completed' && <p className="completion-note">审核已通过，匹配结果已生成。</p>}<div className="disabled-tools"><div>模拟面试<span>功能开发中</span></div><div>历史记录<span>功能开发中</span></div></div>{threadId && <footer>thread_id: {threadId}<br />checkpoint: sqlite · resumable</footer>}</aside>
  </main>
}
function JDResult({ data }: { data: JDParsed }) { return <section className="result-card"><h2>岗位信息</h2><dl><div><dt>核心技能</dt><dd>{data.skills.map((skill) => skill.name).join('、')}<em>证据: {data.skills[0]?.evidence}</em></dd></div><div><dt>职责</dt><dd>{data.responsibilities.join('、')}</dd></div><div><dt>经验要求</dt><dd>{data.experience_requirements.join('；')}</dd></div></dl></section> }
function MatchResultView({ data }: { data: MatchResult }) { return <section className="result-card match-card"><h2>匹配结果 <b>{data.total_score}</b></h2><dl><div><dt>优势</dt><dd>{data.strengths.join('、')}</dd></div><div><dt>待提升</dt><dd>{data.gaps.join('、')}</dd></div><div><dt>建议</dt><dd>{data.recommendations.join('；')}</dd></div></dl></section> }
export default App