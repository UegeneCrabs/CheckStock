import { useEffect, useState } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router'
import type { SessionProfile } from './auth/api'
import { Login } from './auth/Login'
import { useSession } from './auth/session'
import { Header } from './layout/Header'
import { homePath, loginPath, pages, returnPath } from './pages'

function SectionPage({ title, description = 'Эта страница появится в будущем' }: { title: string; description?: string }) {
  useEffect(() => {
    document.title = `РАКЕТА — ${title}`
  }, [title])

  return (
    <section className="panel ui-placeholder">
      <h1>{title}</h1>
      <p>{description}</p>
    </section>
  )
}

function Layout({ profile }: { profile: SessionProfile }) {
  const logout = useSession((state) => state.logout)
  const pending = useSession((state) => state.pending)
  const [logoutFailed, setLogoutFailed] = useState(false)

  async function handleLogout() {
    setLogoutFailed(false)
    try {
      await logout()
    } catch {
      setLogoutFailed(true)
    }
  }

  return (
    <div className="app-shell">
      <Header profile={profile} onLogout={handleLogout} pending={pending} />
      <div className="workspace">
        <main className="content">
          {logoutFailed && <p className="login-error" role="alert">Не удалось выйти. Попробуйте ещё раз.</p>}
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export default function App() {
  const session = useSession((state) => state.session)
  const checkSession = useSession((state) => state.checkSession)
  const location = useLocation()

  useEffect(() => {
    function checkWhenVisible() {
      if (document.visibilityState === 'visible') void checkSession()
    }
    void checkSession()
    window.addEventListener('focus', checkWhenVisible)
    document.addEventListener('visibilitychange', checkWhenVisible)
    return () => {
      window.removeEventListener('focus', checkWhenVisible)
      document.removeEventListener('visibilitychange', checkWhenVisible)
    }
  }, [checkSession])

  if (session.status === 'checking' || session.status === 'unavailable') {
    const checking = session.status === 'checking'
    return (
      <main className="admin-login-shell">
        <div className="admin-login-card">
          <p role={checking ? 'status' : 'alert'}>
            {checking ? 'Проверяем сессию…' : 'Не удалось проверить сессию.'}
          </p>
          {!checking && <button className="btn-primary" type="button" onClick={checkSession}>Повторить</button>}
        </div>
      </main>
    )
  }

  const target = location.pathname + location.search + location.hash
  return (
    <Routes>
      <Route path={loginPath} element={
        session.status === 'authenticated'
          ? <Navigate to={returnPath(location.state)} replace />
          : <Login />
      } />
      <Route element={
        session.status === 'authenticated'
          ? <Layout profile={session.profile} />
          : <Navigate to={loginPath} state={{ returnTo: target }} replace />
      }>
        <Route index element={<Navigate to={homePath} replace />} />
        {pages.map((page) => <Route key={page.path} path={page.path} element={<SectionPage title={page.label} />} />)}
        <Route path="*" element={<SectionPage title="Страница не найдена" description="Выберите раздел в меню." />} />
      </Route>
    </Routes>
  )
}
