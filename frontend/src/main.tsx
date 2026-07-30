import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom'
import Chat from './pages/Chat'
import Lecture from './pages/Lecture'
import Login from './pages/Login'
import Workspace from './pages/Workspace'
import Workspaces from './pages/Workspaces'
import { useAuth } from './lib/auth'
import './index.css'
import './App.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
})

function RequireAuth() {
  const token = useAuth((s) => s.token)
  return token ? <Outlet /> : <Navigate to="/login" replace />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<RequireAuth />}>
            <Route path="/" element={<Workspaces />} />
            <Route path="/w/:id" element={<Workspace />} />
            <Route path="/w/:id/c/:sid" element={<Chat />} />
            <Route path="/w/:id/l/:sid" element={<Lecture />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
