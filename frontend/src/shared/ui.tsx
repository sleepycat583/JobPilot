import { AlertTriangle, CheckCircle2, X } from 'lucide-react'
import type { ReactNode } from 'react'

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="modal-title"><button className="modal-scrim" onClick={onClose} aria-label="关闭弹窗" /><div className="modal"><div className="modal-header"><h2 id="modal-title">{title}</h2><button className="icon-button" type="button" onClick={onClose} aria-label="关闭" title="关闭"><X size={18} /></button></div>{children}</div></div>
}

export function SectionTitle({ title, meta }: { title: string; meta: string }) {
  return <div className="section-title"><h3>{title}</h3><span>{meta}</span></div>
}

export function InfoItem({ label, value }: { label: string; value: string }) {
  return <div className="info-item"><span>{label}</span><strong>{value}</strong></div>
}

export function TimelineItem({ title, subtitle, body }: { title: string; subtitle: string; body: string }) {
  return <div className="timeline-item"><span className="timeline-dot" /><div><h3>{title}</h3><span>{subtitle}</span><p>{body}</p></div></div>
}

export function AnalysisItem({ tone, title, body, source }: { tone: 'positive' | 'warning'; title: string; body: string; source?: string }) {
  return <div className={`analysis-item ${tone}`}><div className="analysis-icon">{tone === 'positive' ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}</div><div><h3>{title}</h3><p>{body}</p>{source && <span>{source}</span>}</div></div>
}

export function EmptyState({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return <div className="content-section empty-state"><h3>{title}</h3><p>{detail}</p>{action}</div>
}
