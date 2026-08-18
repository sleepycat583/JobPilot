import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { ChatPage } from './features/chat/ChatPage'
import { InterviewPage } from './features/interview/InterviewPage'
import { JDPage } from './features/jd/JDPage'
import { MatchPage } from './features/match/MatchPage'
import { ResumePage } from './features/resumes/ResumePage'

export default function App() {
  return <Routes>
    <Route element={<AppShell />}>
      <Route index element={<ChatPage />} />
      <Route path="resumes" element={<ResumePage />} />
      <Route path="jd" element={<JDPage />} />
      <Route path="match" element={<MatchPage />} />
      <Route path="interview" element={<InterviewPage />} />
    </Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
}
