import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArchiveRestore, CheckCircle2, CircleAlert, Download, HardDrive, LoaderCircle, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../lib/api/client'
import type { LocalCleanupPreview, LocalDataSummary } from '../../shared/types'
import { Modal, SectionTitle } from '../../shared/ui'

const retentionOptions = [7, 30, 90, 180]

type SettingsFeedback = {
  tone: 'success' | 'info' | 'error'
  message: string
}

function bytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const [retentionDays, setRetentionDays] = useState(30)
  const [cleanupOpen, setCleanupOpen] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [feedback, setFeedback] = useState<SettingsFeedback | null>(null)
  const summary = useQuery({ queryKey: ['local-data-summary'], queryFn: api.getLocalDataSummary })
  const preview = useQuery({ queryKey: ['local-cleanup-preview', retentionDays], queryFn: () => api.getLocalCleanupPreview(retentionDays) })
  const backup = useMutation({ mutationFn: api.downloadLocalBackup })
  const refreshStatus = async () => {
    setFeedback(null)
    const results = await Promise.all([summary.refetch(), preview.refetch()])
    const failed = results.find((result) => result.error)
    setFeedback(failed?.error
      ? { tone: 'error', message: failed.error.message }
      : { tone: 'success', message: '本地数据状态已刷新。' })
  }
  const cleanup = useMutation({
    mutationFn: () => api.cleanupLocalHistory(retentionDays),
    onMutate: () => setFeedback(null),
    onSuccess: async (result) => {
      const total = result.terminal_jobs + result.idempotency_records + result.execution_events
      setCleanupOpen(false)
      setConfirmation('')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['local-data-summary'] }),
        queryClient.invalidateQueries({ queryKey: ['local-cleanup-preview'] }),
      ])
      setFeedback(total > 0
        ? { tone: 'success', message: `已清理 ${total} 条超过 ${retentionDays} 天的运行记录。` }
        : { tone: 'info', message: `没有清理任何记录：当前没有超过 ${retentionDays} 天的可清理运行记录。会话、简历、JD 和报告会保留。` })
    },
  })

  if (summary.isLoading || !summary.data) return <div className="page-view settings-view"><p className="settings-loading"><LoaderCircle className="spin" size={18} />正在读取本地数据状态</p></div>
  if (summary.error) return <div className="page-view settings-view"><p className="inline-error">{summary.error.message}</p></div>

  return <div className="page-view settings-view">
    <div className="settings-heading"><div><span className="eyebrow">本地工作区</span><h2>隐私与数据</h2></div><button className="secondary-button" type="button" onClick={() => { void refreshStatus() }} disabled={summary.isRefetching || preview.isRefetching}>{summary.isRefetching || preview.isRefetching ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}{summary.isRefetching || preview.isRefetching ? '正在刷新' : '刷新状态'}</button></div>
    {feedback && <SettingsFeedbackNotice feedback={feedback} />}
    <section className="settings-band storage-overview"><div className="settings-band-title"><HardDrive size={18} /><div><h3>本地存储</h3><p>业务记录、文件、工作流状态和向量索引均保存在当前电脑。</p></div></div><StorageGrid summary={summary.data} /></section>
    <div className="settings-grid">
      <section className="settings-band"><SectionTitle title="数据备份" meta="ZIP" /><div className="settings-action"><div><strong>创建本地备份</strong><p>生成业务数据库、工作流状态、上传文件和向量索引的一致性副本。</p></div><button className="primary-button" type="button" onClick={() => backup.mutate()} disabled={backup.isPending}>{backup.isPending ? <LoaderCircle className="spin" size={17} /> : <Download size={17} />}下载备份</button></div>{backup.error && <p className="inline-error">{backup.error.message}</p>}<div className="offline-restore"><ArchiveRestore size={16} /><span>恢复备份需先关闭本地服务，再使用离线恢复命令。</span></div></section>
      <section className="settings-band"><SectionTitle title="模型处理" meta={summary.data.model_processing === 'external_model' ? '外部模型' : '本地 Stub'} /><div className={`processing-status ${summary.data.model_processing}`}><ShieldCheck size={17} /><div><strong>{summary.data.model_processing === 'external_model' ? '当前任务会调用外部兼容模型' : '当前使用本地 Stub 模式'}</strong><p>{summary.data.model_processing === 'external_model' ? '模型调用遵循当前 .env 配置；密钥不会写入工作区数据或备份清单。' : '当前不会发起外部模型调用。'}</p></div></div></section>
    </div>
    <section className="settings-band cleanup-band"><SectionTitle title="运行记录清理" meta="可预览" /><div className="cleanup-controls"><label htmlFor="retention-days">保留期限</label><select id="retention-days" value={retentionDays} onChange={(event) => { setRetentionDays(Number(event.target.value)); setFeedback(null) }}>{retentionOptions.map((days) => <option key={days} value={days}>{days} 天</option>)}</select><button className="secondary-button danger" type="button" onClick={() => setCleanupOpen(true)} disabled={preview.isLoading}><Trash2 size={16} />清理运行记录</button></div><CleanupPreview preview={preview.data} loading={preview.isLoading} /></section>
    {cleanupOpen && <CleanupModal retentionDays={retentionDays} confirmation={confirmation} setConfirmation={setConfirmation} preview={preview.data} pending={cleanup.isPending} error={cleanup.error?.message} onClose={() => setCleanupOpen(false)} onConfirm={() => cleanup.mutate()} />}
  </div>
}

function SettingsFeedbackNotice({ feedback }: { feedback: SettingsFeedback }) {
  const Icon = feedback.tone === 'success' ? CheckCircle2 : CircleAlert
  return <p className={`settings-feedback ${feedback.tone}`} role={feedback.tone === 'error' ? 'alert' : 'status'}><Icon size={16} />{feedback.message}</p>
}

function StorageGrid({ summary }: { summary: LocalDataSummary }) {
  const items = [
    ['简历与 JD', summary.counts.resumes + summary.counts.job_descriptions, `${summary.counts.resumes} 份简历 · ${summary.counts.job_descriptions} 个 JD`],
    ['会话与报告', summary.counts.conversations + summary.counts.match_reports + summary.counts.interview_reports, `${summary.counts.conversations} 个会话 · ${summary.counts.match_reports + summary.counts.interview_reports} 份报告`],
    ['上传文件', summary.counts.uploaded_files, bytes(summary.storage.uploads_bytes)],
    ['本地存储', bytes(summary.storage.business_database_bytes + summary.storage.checkpoint_database_bytes + summary.storage.uploads_bytes + summary.storage.vector_index_bytes), `数据库 ${bytes(summary.storage.business_database_bytes + summary.storage.checkpoint_database_bytes)} · 索引 ${bytes(summary.storage.vector_index_bytes)}`],
  ]
  return <div className="storage-grid">{items.map(([label, value, detail]) => <div key={String(label)}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>)}</div>
}

function CleanupPreview({ preview, loading }: { preview: LocalCleanupPreview | undefined; loading: boolean }) {
  if (loading || !preview) return <p className="settings-loading"><LoaderCircle className="spin" size={15} />正在计算可清理历史</p>
  const total = preview.terminal_jobs + preview.idempotency_records + preview.execution_events
  if (total === 0) return <div className="cleanup-preview"><strong>当前没有可清理记录</strong><span>仅清理超过 {preview.retention_days} 天的终态任务、请求幂等记录和事件记录。</span><span>会话、简历、JD 和报告将保留。</span></div>
  return <div className="cleanup-preview"><strong>{total} 条可清理记录</strong><span>终态任务 {preview.terminal_jobs}</span><span>请求幂等记录 {preview.idempotency_records}</span><span>事件记录 {preview.execution_events}</span></div>
}

function CleanupModal({ retentionDays, confirmation, setConfirmation, preview, pending, error, onClose, onConfirm }: { retentionDays: number; confirmation: string; setConfirmation: (value: string) => void; preview: LocalCleanupPreview | undefined; pending: boolean; error?: string; onClose: () => void; onConfirm: () => void }) {
  const total = preview ? preview.terminal_jobs + preview.idempotency_records + preview.execution_events : 0
  return <Modal title="确认清理运行记录" onClose={onClose}><div className="cleanup-modal"><p>将删除超过 {retentionDays} 天的终态任务、请求幂等记录与事件记录。不会删除会话、简历、JD、上传文件、匹配报告、面试复盘或向量索引。</p><strong>当前可清理 {total} 条记录</strong><label htmlFor="cleanup-confirmation">输入 DELETE_LOCAL_HISTORY 以确认</label><input id="cleanup-confirmation" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoFocus />{error && <p className="inline-error">{error}</p>}<div className="modal-actions"><button className="secondary-button" type="button" onClick={onClose}>取消</button><button className="primary-button danger-fill" type="button" onClick={onConfirm} disabled={confirmation !== 'DELETE_LOCAL_HISTORY' || pending}>{pending ? <LoaderCircle className="spin" size={17} /> : <Trash2 size={17} />}确认清理</button></div></div></Modal>
}
