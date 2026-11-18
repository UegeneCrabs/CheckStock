import { useEffect, useState } from 'react'
import type { SubmitEvent } from 'react'
import { AuthHttpError } from './api'
import { useSession } from './session'

function loginError(error: unknown) {
  if (error instanceof AuthHttpError) {
    if (error.status === 401) return 'Неверный логин или пароль'
    if (error.status === 422) return 'Проверьте логин и пароль'
    if (error.status === 429) return 'Слишком много попыток входа. Попробуйте позже.'
  }
  return 'Не удалось войти. Попробуйте ещё раз.'
}

export function Login() {
  const login = useSession((state) => state.login)
  const pending = useSession((state) => state.pending)
  const [error, setError] = useState('')

  useEffect(() => {
    document.title = 'РАКЕТА — Вход'
  }, [])

  async function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) return
    const form = event.currentTarget
    const fields = new FormData(form)
    setError('')
    try {
      await login(String(fields.get('login') ?? ''), String(fields.get('password') ?? ''))
    } catch (failure) {
      setError(loginError(failure))
    }
  }

  return (
    <main className="admin-login-shell">
      <form className="admin-login-card" method="post" onSubmit={submit} aria-busy={pending}>
        <div className="login-brand">
          <span className="login-brand-mark">Р</span>
          <span>РАКЕТА</span>
        </div>
        <span className="login-kicker">СИСТЕМА УПРАВЛЕНИЯ ЗАПАСАМИ</span>
        <h1>Вход в РАКЕТУ</h1>
        <p>Остатки, склады и перемещения доступны только сотрудникам.</p>
        {error && <p className="login-error" role="alert">{error}</p>}

        <div className="admin-login-form">
          <label className="login-field">
            <span>Логин</span>
            <input name="login" autoComplete="username" autoFocus required maxLength={100} />
          </label>
          <label className="login-field">
            <span>Пароль</span>
            <input type="password" name="password" autoComplete="current-password" required maxLength={1024} />
          </label>
          <button className="btn-primary" type="submit" disabled={pending}>
            {pending ? 'Вход…' : 'Войти'}
          </button>
        </div>
        <span className="login-security-note">Защищённый вход · рабочие данные остаются внутри системы</span>
      </form>
    </main>
  )
}
