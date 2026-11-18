import { create } from 'zustand'
import { AuthHttpError, createSession, fetchSession, removeSession } from './api'
import type { SessionProfile } from './api'

type Session =
  | { status: 'checking' | 'anonymous' | 'unavailable' }
  | { status: 'authenticated'; profile: SessionProfile }

interface SessionStore {
  session: Session
  pending: boolean
  checkSession: () => Promise<void>
  login: (login: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const useSession = create<SessionStore>()((set, get) => {
  let check: AbortController | undefined

  async function changeSession(change: () => Promise<Session>) {
    if (get().pending) return
    check?.abort()
    check = undefined
    set({ pending: true })
    try {
      set({ session: await change() })
    } finally {
      set({ pending: false })
    }
  }

  return {
    session: { status: 'checking' },
    pending: false,
    async checkSession() {
      if (check || get().pending) return
      const current = new AbortController()
      check = current
      if (get().session.status === 'unavailable') {
        set({ session: { status: 'checking' } })
      }
      try {
        const profile = await fetchSession(current.signal)
        if (!current.signal.aborted) set({ session: { status: 'authenticated', profile } })
      } catch (error) {
        if (!current.signal.aborted) {
          const status = error instanceof AuthHttpError && error.status === 401 ? 'anonymous' : 'unavailable'
          set({ session: { status } })
        }
      } finally {
        if (check === current) check = undefined
      }
    },
    login: (login, password) =>
      changeSession(async () => ({ status: 'authenticated', profile: await createSession(login, password) })),
    logout: () =>
      changeSession(async () => {
        await removeSession()
        return { status: 'anonymous' }
      }),
  }
})
