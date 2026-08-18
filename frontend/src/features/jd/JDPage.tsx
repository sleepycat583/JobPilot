import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, FileSearch, History, LoaderCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useWorkspace } from '../../app/WorkspaceProvider'
import { api, pollJob } from '../../lib/api/client'
import type { JDRead, JobRead } from '../../shared/types'
import { EmptyState, InfoItem, SectionTitle } from '../../shared/ui'

type Tab = 'summary' | 'skills' | 'evidence'
const defaultText = '负责公司交易平台核心模块建设；要求 3 年以上 Java 开发经验，熟悉 Spring Boot、MySQL、Redis 和消息队列；有高并发系统调优经验优先。'

export function JDPage() {
  const queryClient = useQueryClient()
  const { selectedJDId, setSelectedJDId } = useWorkspace()
  const [text, setText] = useState(defaultText)
  const [tab, setTab] = useState<Tab>('summary')
  const [job, setJob] = useState<JobRead | null>(null)
  const jds = useQuery({ queryKey: ['jds'], queryFn: api.listJDs })
  const selected = jds.data?.find((item) => item.id === selectedJDId) ?? jds.data?.[0]
  useEffect(() => {
    if (!selectedJDId && jds.data?.[0]) setSelectedJDId(jds.data[0].id)
  }, [jds.data, selectedJDId, setSelectedJDId])

  const analyze = useMutation({
    mutationFn: async () => {
      const accepted = await api.createJD(text.trim())
      setJob({ id: accepted.job_id, kind: 'jd_parse', status: 'queued', progress: 0, result: null, error: null, created_at: '', updated_at: '' })
      await pollJob(accepted.job_id, setJob)
      return accepted.resource_id!
    },
    onSuccess: async (resourceId) => {
      await queryClient.invalidateQueries({ queryKey: ['jds'] })
      setSelectedJDId(resourceId)
    },
  })

  return <div className="page-view jd-view">
    <div className="page-toolbar"><div><h2>职位描述</h2><p>粘贴完整 JD，分析结果会保存为可复用版本。</p></div><div className="toolbar-buttons"><button className="secondary-button" type="button"><History size={16} />{jds.data?.length ?? 0} 条历史 JD</button><button className="primary-button" type="button" onClick={() => analyze.mutate()} disabled={text.trim().length < 20 || analyze.isPending}>{analyze.isPending ? <LoaderCircle className="spin" size={17} /> : <FileSearch size={17} />}{analyze.isPending ? `分析中 ${job?.progress ?? 0}%` : '开始分析'}</button></div></div>
    <div className="jd-editor-band"><label htmlFor="jd-input">JD 原文</label><textarea id="jd-input" value={text} onChange={(event) => setText(event.target.value)} /><div className="editor-footer"><span>{text.length} 字</span><span>{analyze.error?.message ?? '默认不联网补充公司信息'}</span></div></div>
    <section className="result-section">{selected ? <JDResult jd={selected} tab={tab} setTab={setTab} /> : <EmptyState title="还没有分析结果" detail="输入完整职位描述并开始分析，结果将保存到本地工作区。" />}</section>
  </div>
}

function JDResult({ jd, tab, setTab }: { jd: JDRead; tab: Tab; setTab: (tab: Tab) => void }) {
  const parsed = jd.parsed
  return <><div className="result-heading"><div><span className="eyebrow">分析结果</span><h2>{parsed?.job_title ?? jd.title}</h2><p>中高级 · 来源为当前 JD</p></div>{parsed?.evidence_preserved && <span className="verified-badge"><CheckCircle2 size={15} />已保留原文证据</span>}</div><div className="tabs">{([['summary', '岗位概要'], ['skills', '技能要求'], ['evidence', '面试重点']] as const).map(([id, label]) => <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</div>{tab === 'summary' && <div className="content-section"><SectionTitle title="核心职责" meta={`${parsed?.responsibilities?.length ?? 0} 项`} /><ol className="number-list">{parsed?.responsibilities?.map((item) => <li key={item}>{item}</li>)}</ol><SectionTitle title="基本要求" meta="明确要求" /><div className="requirement-list"><InfoItem label="岗位级别" value={parsed?.seniority ?? '待确认'} /><InfoItem label="技术基础" value={parsed?.required_skills?.join('、') ?? '待提取'} /></div></div>}{tab === 'skills' && <div className="content-section"><SkillGroup title="必备技能" items={parsed?.required_skills ?? []} tone="required" /><SkillGroup title="加分技能" items={parsed?.preferred_skills ?? []} tone="preferred" /><SkillGroup title="合理推断" items={parsed?.inferred_skills ?? []} tone="inferred" /></div>}{tab === 'evidence' && <div className="content-section"><SectionTitle title="建议重点准备" meta={`${parsed?.interview_focus?.length ?? 0} 个主题`} /><div className="focus-list">{parsed?.interview_focus?.map((item, index) => <InfoItem key={item} label={String(index + 1).padStart(2, '0')} value={item} />)}</div></div>}</>
}

function SkillGroup({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  return <div className="requirement-group"><SectionTitle title={title} meta={`${items.length} 项`} /><div className="tag-list">{items.map((item) => <span className={`tag ${tone}`} key={item}>{item}</span>)}</div></div>
}
