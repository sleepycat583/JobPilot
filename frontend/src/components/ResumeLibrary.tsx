/** 左侧简历库：显示版本、上传 TXT 文件及失败索引的重试操作。 */
import { useRef } from 'react'

import type { ResumeDto } from '../types'

type ResumeLibraryProps = {
  resumes: ResumeDto[]
  selectedResumeId: string
  isLoading: boolean
  isUploading: boolean
  retryingResumeId?: string | null
  error: string | null
  onSelect: (resume: ResumeDto) => void
  onClearSelection: () => void
  onUpload: (file: File) => void
  onRetry: (resumeId: string) => void
  onRefresh: () => void
  onStartInterview?: () => void
}

const STATUS_LABEL: Record<ResumeDto['index_status'], string> = {
  pending: '等待建立索引',
  indexing: '正在建立索引',
  indexed: '已建立索引',
  failed: '索引失败',
}

function formatFileSize(fileSize: number): string {
  return fileSize < 1024 * 1024 ? `${Math.max(1, Math.ceil(fileSize / 1024))} KB` : `${(fileSize / 1024 / 1024).toFixed(1)} MB`
}

/**
 * 呈现简历版本列表并转发用户操作。
 *
 * 参数中的状态和回调由 useResumeLibrary 提供，组件不直接调用后端。
 */
export function ResumeLibrary({ resumes, selectedResumeId, isLoading, isUploading, retryingResumeId, error, onSelect, onClearSelection, onUpload, onRetry, onRefresh, onStartInterview }: ResumeLibraryProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  return <aside className="resume-sidebar" aria-label="简历库">
    <div className="sidebar-heading"><h1>简历库</h1><button type="button" className="icon-button" onClick={onRefresh} aria-label="刷新简历库" title="刷新简历库">↻</button></div>
    <input ref={inputRef} className="visually-hidden" type="file" accept=".txt,text/plain" onChange={(event) => {
      const file = event.target.files?.[0]
      if (file) onUpload(file)
      event.currentTarget.value = ''
    }} />
    {isLoading ? <p className="empty-hint">正在加载简历库...</p> : resumes.length > 0 ? (
      <div className="resume-list">
        {resumes.map((resume) => <div className="resume-entry" key={resume.resume_id}>
          <button type="button" className={`resume-item ${selectedResumeId === resume.resume_id ? 'selected' : ''}`} disabled={resume.index_status !== 'indexed'} onClick={() => onSelect(resume)}>
            <strong>v{resume.display_version} · {resume.file_name}</strong>
            <span className={`resume-status ${resume.index_status}`}><i />{STATUS_LABEL[resume.index_status]} · {formatFileSize(resume.file_size)}</span>
          </button>
          {resume.index_status === 'failed' && <div className="resume-failure"><span>{resume.error_message ?? '建立索引时发生错误。'}</span><button type="button" className="text-button" disabled={retryingResumeId === resume.resume_id} onClick={() => onRetry(resume.resume_id)}>{retryingResumeId === resume.resume_id ? '正在重试...' : '重试索引'}</button></div>}
        </div>)}
      </div>
    ) : <p className="empty-hint">暂无简历，请上传并完成索引。</p>}
    {selectedResumeId && <button type="button" className="text-button clear-selection-button" onClick={onClearSelection}>取消选择简历</button>}
    {error && <p className="resume-error" role="alert">{error}</p>}
    <button type="button" className="upload-button" disabled={isUploading} onClick={() => inputRef.current?.click()}>
      <span>+</span>{isUploading ? '正在上传...' : '上传新版本简历'}
    </button>
      <div className="disabled-tools">
        <button type="button" className="tool-button" onClick={onStartInterview} disabled={!onStartInterview}>模拟面试<span>按 JD 或匹配结果开始</span></button>
      <div>历史记录<span>功能开发中</span></div>
    </div>
  </aside>
}