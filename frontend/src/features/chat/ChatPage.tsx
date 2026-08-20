import { useMutation, useQueryClient } from '@tanstack/react-query'
import { BarChart3, BriefcaseBusiness, ClipboardList, FileText, LoaderCircle, Mic2, Paperclip, Send, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspace } from '../../app/WorkspaceProvider'
import { api } from '../../lib/api/client'

export function ChatPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { threadId, state, selectedResumeId, selectedJDId, cancelActiveRun } = useWorkspace()
  const [composer, setComposer] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  const send = useMutation({
    mutationFn: (content: string) => api.sendMessage(threadId!, {
      content,
      ...(selectedResumeId ? { resume_id: selectedResumeId } : {}),
      ...(selectedJDId ? { jd_id: selectedJDId } : {}),
    }),
    onSuccess: () => {
      setComposer('')
      void queryClient.invalidateQueries({ queryKey: ['thread-state', threadId] })
    },
  })
  const running = state?.status === 'running' || send.isPending

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [running, state?.messages])

  const submit = () => {
    const content = composer.trim()
    if (content && threadId && !running) send.mutate(content)
  }

  return <div className="workbench-view">
    <div className="conversation-scroll"><div className="conversation-column">
      {(state?.messages.length ?? 0) <= 1 && <div className="conversation-heading"><h2>从一项具体任务开始</h2><p>当前资料会在会话中持续关联，不需要重复上传或粘贴。</p><div className="quick-actions">
        <button type="button" onClick={() => navigate('/resumes')}><FileText size={18} /><span><strong>解析简历</strong><small>上传并检查结构化结果</small></span></button>
        <button type="button" onClick={() => navigate('/jd')}><ClipboardList size={18} /><span><strong>分析 JD</strong><small>提取职责、技能与重点</small></span></button>
        <button type="button" onClick={() => navigate('/match')}><BarChart3 size={18} /><span><strong>岗位匹配</strong><small>查看差距与简历证据</small></span></button>
        <button type="button" onClick={() => navigate('/interview')}><Mic2 size={18} /><span><strong>模拟面试</strong><small>按目标岗位进行练习</small></span></button>
      </div></div>}
      <div className="message-list" aria-live="polite">
        {state?.messages.map((message) => <article key={message.id} className={`message ${message.role}`}><div className="message-avatar">{message.role === 'assistant' ? 'AI' : '林'}</div><div className="message-content"><div className="message-meta"><strong>{message.role === 'assistant' ? '求职助手' : '你'}</strong><span>{new Date(message.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span></div><p>{message.content}</p></div></article>)}
        {running && <article className="message assistant"><div className="message-avatar">AI</div><div className="message-content"><div className="message-meta"><strong>求职助手</strong></div><div className="typing-indicator"><span /><span /><span /></div><button className="text-button" type="button" onClick={() => void cancelActiveRun()}><XCircle size={15} />取消本轮任务</button></div></article>}
        {send.error && <p className="inline-error">{send.error.message}</p>}
        <div ref={endRef} />
      </div>
    </div></div>
    <div className="composer-wrap">
      {(selectedResumeId || selectedJDId) && <div className="context-strip">{selectedResumeId && <span><FileText size={14} /> 已关联简历</span>}{selectedJDId && <span><BriefcaseBusiness size={14} /> 已关联 JD</span>}</div>}
      <div className="composer"><button className="icon-button" type="button" aria-label="添加资料" title="添加资料" onClick={() => navigate('/resumes')}><Paperclip size={18} /></button><textarea value={composer} onChange={(event) => setComposer(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() } }} placeholder="描述你的求职需求..." rows={2} aria-label="消息输入" /><button className="send-button" type="button" onClick={submit} disabled={!composer.trim() || running || !threadId}>{running ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}<span>发送</span></button></div>
      <p className="composer-note">AI 输出仅用于求职准备参考，请结合实际经历核对。</p>
    </div>
  </div>
}
