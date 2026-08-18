import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, FileText, LoaderCircle, RefreshCw, ShieldCheck, Upload } from 'lucide-react'
import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { useWorkspace } from '../../app/WorkspaceProvider'
import { api, pollJob } from '../../lib/api/client'
import type { JobRead, ResumeRead } from '../../shared/types'
import { EmptyState, InfoItem, SectionTitle, TimelineItem } from '../../shared/ui'

type Tab = 'overview' | 'experience' | 'projects' | 'skills'

export function ResumePage() {
  const fileRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const { selectedResumeId, setSelectedResumeId } = useWorkspace()
  const [tab, setTab] = useState<Tab>('overview')
  const [job, setJob] = useState<JobRead | null>(null)
  const [fileName, setFileName] = useState('')
  const resumes = useQuery({ queryKey: ['resumes'], queryFn: api.listResumes })
  const selected = resumes.data?.find((item) => item.id === selectedResumeId) ?? resumes.data?.[0]

  useEffect(() => {
    if (!selectedResumeId && resumes.data?.[0]) setSelectedResumeId(resumes.data[0].id)
  }, [resumes.data, selectedResumeId, setSelectedResumeId])

  const upload = useMutation({
    mutationFn: async (file: File) => {
      setFileName(file.name)
      setJob({ id: '', kind: 'resume_parse', status: 'queued', progress: 0, result: null, error: null, created_at: '', updated_at: '' })
      const accepted = await api.uploadResume(file)
      const completed = await pollJob(accepted.job_id, setJob)
      return { completed, resourceId: accepted.resource_id! }
    },
    onSuccess: async ({ resourceId }) => {
      await queryClient.invalidateQueries({ queryKey: ['resumes'] })
      setSelectedResumeId(resourceId)
    },
  })
  const handleFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) upload.mutate(file)
    event.target.value = ''
  }

  return <div className="page-view resume-view">
    <div className="page-toolbar"><div><h2>简历版本</h2><p>结构化内容与原始文件分开保存，修改后生成新版本。</p></div><input ref={fileRef} hidden type="file" accept=".pdf,.docx,.txt" onChange={handleFile} /><button className="primary-button" type="button" onClick={() => fileRef.current?.click()} disabled={upload.isPending}>{upload.isPending ? <LoaderCircle className="spin" size={17} /> : <Upload size={17} />}上传新版本</button></div>
    {(job || upload.error) && <div className={`upload-progress ${job?.status === 'completed' ? 'complete' : ''}`}><div className="upload-file-icon"><FileText size={20} /></div><div className="upload-copy"><strong>{fileName || '上传任务'}</strong><span>{upload.error ? upload.error.message : job?.status === 'completed' ? '解析完成，已创建新版本' : `服务端处理中 · ${job?.progress ?? 0}%`}</span></div>{job?.status === 'completed' ? <CheckCircle2 size={20} /> : <LoaderCircle className="spin" size={20} />}</div>}
    <div className="split-layout">
      <aside className="record-list">{resumes.data?.map((resume) => <button key={resume.id} className={resume.id === selected?.id ? 'record active' : 'record'} type="button" onClick={() => setSelectedResumeId(resume.id)}><div className="record-title"><FileText size={17} /><strong>{resume.display_name}</strong></div><p>{resume.file_name}</p><div className="record-meta"><span className={`status-dot ${resume.status === 'indexed' ? '' : 'muted'}`} />{resume.status === 'indexed' ? '已建立索引' : '处理中'} <span>{formatSize(resume.size_bytes)}</span></div></button>)}</aside>
      <section className="document-pane">{selected ? <><div className="document-header"><div><div className="eyebrow">当前版本</div><h2>{selected.display_name}</h2><p>更新于 {new Date(selected.updated_at).toLocaleString('zh-CN')} · {selected.structured?.chunk_count ?? 0} 个可检索片段</p></div><button className="secondary-button" type="button" onClick={() => fileRef.current?.click()}><RefreshCw size={16} />上传新版本</button></div><div className="tabs" role="tablist">{([['overview', '概要'], ['experience', '工作经历'], ['projects', '项目经历'], ['skills', '技能']] as const).map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</div><ResumeContent resume={selected} tab={tab} /></> : <EmptyState title="还没有简历" detail="上传 PDF、DOCX 或 TXT 文件后，服务端会异步解析并生成结构化版本。" action={<button className="primary-button" type="button" onClick={() => fileRef.current?.click()}><Upload size={17} />上传简历</button>} />}</section>
    </div>
  </div>
}

function ResumeContent({ resume, tab }: { resume: ResumeRead; tab: Tab }) {
  const structured = resume.structured
  if (tab === 'skills') return <div className="content-section"><SectionTitle title="技能结构" meta={`${structured?.skills?.length ?? 0} 项`} /><div className="skill-grid">{structured?.skills?.map((skill) => <span key={skill}>{skill}</span>)}</div></div>
  if (tab === 'experience') return <div className="content-section"><SectionTitle title="工作经历" meta="Mock 结构" /><TimelineItem title="后端开发工程师" subtitle="结构化示例" body="当前阶段保留 UI 与契约，真实经历抽取将在简历 Worker 接入后替换。" /></div>
  if (tab === 'projects') return <div className="content-section"><SectionTitle title="项目经历" meta="Mock 结构" /><TimelineItem title="项目证据" subtitle="结构化示例" body="这里将展示可定位到原始简历片段的项目职责、技术决策和量化结果。" /></div>
  return <div className="content-section"><SectionTitle title="个人概要" meta={structured ? '已解析' : '处理中'} /><p className="document-copy">{structured?.profile ?? '服务端正在生成结构化结果。'}</p><div className="info-grid"><InfoItem label="目标岗位" value={structured?.target_role ?? '待补充'} /><InfoItem label="工作年限" value={structured?.years ? `${structured.years} 年` : '待补充'} /><InfoItem label="最高学历" value={structured?.education ?? '待补充'} /><InfoItem label="求职城市" value={structured?.locations?.join(' / ') ?? '待补充'} /></div>{structured?.privacy_filtered && <div className="notice"><ShieldCheck size={18} /><div><strong>隐私信息已隔离</strong><p>联系方式不会进入向量检索内容，也不会发送到无关任务。</p></div></div>}</div>
}

function formatSize(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
