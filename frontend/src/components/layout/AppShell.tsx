import {
  AlertTriangle, ArrowLeft, BarChart3, BriefcaseBusiness, Check, CheckCircle2,
  Circle, ClipboardList, FileText, Menu, MessageSquareText, Mic2, MoreHorizontal,
  PanelRight, Plus, Search, ShieldCheck, X, LoaderCircle,
} from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useWorkspace } from '../../app/WorkspaceProvider'
import type { TaskView, View } from '../../shared/types'

const routes: Record<View, string> = { workbench: '/', resumes: '/resumes', jd: '/jd', match: '/match', interview: '/interview' }
const navItems = [
  { id: 'workbench' as const, label: '对话', icon: MessageSquareText },
  { id: 'resumes' as const, label: '简历库', icon: FileText },
  { id: 'jd' as const, label: 'JD 分析', icon: ClipboardList },
  { id: 'match' as const, label: '匹配报告', icon: BarChart3 },
  { id: 'interview' as const, label: '模拟面试', icon: Mic2 },
]
const titles: Record<string, string> = { '/': '对话工作台', '/resumes': '简历库', '/jd': 'JD 分析', '/match': '匹配报告', '/interview': '模拟面试' }
const idleTask: TaskView = { status: 'idle', title: '等待任务', detail: '发送消息后，这里会显示处理进度。', steps: [] }

export function AppShell() {
  const [navOpen, setNavOpen] = useState(false)
  const [contextOpen, setContextOpen] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  const { state, error } = useWorkspace()
  const task = state?.task ?? (error ? { status: 'failed' as const, title: '服务连接失败', detail: error.message, steps: [] } : idleTask)

  return <div className="app-shell">
    <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
    <div className="app-main">
      <header className="topbar">
        <div className="topbar-leading">
          <button className="icon-button mobile-menu" type="button" aria-label="打开导航" title="打开导航" onClick={() => setNavOpen(true)}><Menu size={19} /></button>
          {location.pathname !== '/' && <button className="icon-button back-button" type="button" aria-label="返回对话" title="返回对话" onClick={() => navigate('/')}><ArrowLeft size={18} /></button>}
          <div><p className="topbar-kicker">求职工作台</p><h1>{titles[location.pathname] ?? '求职工作台'}</h1></div>
        </div>
        <div className="topbar-actions"><span className="save-state"><Check size={14} /> 已保存</span><button className="icon-button" type="button" aria-label="查看当前任务" title="查看当前任务" onClick={() => setContextOpen(true)}><PanelRight size={18} /></button></div>
      </header>
      <main className="content-area"><section className="primary-pane"><Outlet /></section><TaskPanel task={task} mobileOpen={contextOpen} onClose={() => setContextOpen(false)} /></main>
    </div>
  </div>
}

function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <>
    <aside className={`sidebar ${open ? 'sidebar-open' : ''}`}>
      <div className="brand"><div className="brand-mark"><BriefcaseBusiness size={20} /></div><div><strong>求职工作台</strong><span>AI Career Workspace</span></div><button className="icon-button sidebar-close" onClick={onClose} aria-label="关闭导航" title="关闭导航"><X size={18} /></button></div>
      <NavLink className="new-chat-button" to="/" onClick={onClose}><Plus size={17} /><span>当前对话</span></NavLink>
      <nav className="main-nav" aria-label="主导航">{navItems.map(({ id, label, icon: Icon }) => <NavLink key={id} to={routes[id]} end={id === 'workbench'} className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'} onClick={onClose}><Icon size={18} /><span>{label}</span></NavLink>)}</nav>
      <div className="sidebar-history"><div className="section-label-row"><span className="section-label">最近会话</span><button className="icon-button compact" type="button" aria-label="搜索会话" title="搜索会话"><Search size={15} /></button></div><NavLink className="history-item active" to="/"><span>当前求职准备</span><small>今天</small></NavLink></div>
      <div className="sidebar-footer"><div className="avatar">林</div><div><strong>林同学</strong><span>本地工作区</span></div><button className="icon-button compact" type="button" aria-label="更多设置" title="更多设置"><MoreHorizontal size={17} /></button></div>
    </aside>
    {open && <button className="mobile-scrim" aria-label="关闭导航" onClick={onClose} />}
  </>
}

function TaskPanel({ task, mobileOpen, onClose }: { task: TaskView; mobileOpen: boolean; onClose: () => void }) {
  const { selectedResumeId, selectedJDId } = useWorkspace()
  return <>
    <aside className={`context-panel ${mobileOpen ? 'context-panel-open' : ''}`}>
      <div className="context-panel-header"><div><span className="eyebrow">当前上下文</span><h2>任务与资料</h2></div><button className="icon-button context-close" type="button" onClick={onClose} aria-label="关闭当前任务" title="关闭当前任务"><X size={18} /></button></div>
      <section className="context-section"><span className="section-label">当前资料</span><div className="context-record"><FileText size={17} /><div><strong>{selectedResumeId ? '已选择简历' : '尚未选择简历'}</strong><span>{selectedResumeId ? '结构化资料已关联' : '请在简历库上传'}</span></div>{selectedResumeId && <CheckCircle2 size={16} />}</div><div className="context-record"><BriefcaseBusiness size={17} /><div><strong>{selectedJDId ? '已选择目标 JD' : '尚未选择 JD'}</strong><span>{selectedJDId ? '职位分析已关联' : '请先分析职位描述'}</span></div>{selectedJDId && <CheckCircle2 size={16} />}</div></section>
      <section className="context-section"><span className="section-label">当前任务</span><div className={`task-status task-${task.status}`}><div className="task-status-icon">{task.status === 'running' ? <LoaderCircle className="spin" size={19} /> : task.status === 'completed' ? <CheckCircle2 size={19} /> : task.status === 'interrupted' || task.status === 'failed' ? <AlertTriangle size={19} /> : <Circle size={18} />}</div><div><strong>{task.title}</strong><p>{task.detail}</p></div></div>{task.steps.length > 0 && <div className="task-steps">{task.steps.map((step) => <div key={step.label} className={step.status}><span>{step.status === 'done' ? <Check size={13} /> : step.status === 'active' ? <LoaderCircle className={task.status === 'running' ? 'spin' : ''} size={13} /> : null}</span><p>{step.label}</p></div>)}</div>}</section>
      <div className="privacy-note"><ShieldCheck size={16} /><span>这里只展示面向用户的任务状态与结果。</span></div>
    </aside>
    {mobileOpen && <button className="mobile-scrim context-scrim" aria-label="关闭当前任务" onClick={onClose} />}
  </>
}
