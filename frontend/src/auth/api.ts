export interface SessionProfile {
  readonly id: number
  readonly full_name: string
  readonly first_store_slug: string | null
}

export class AuthHttpError extends Error {
  readonly status: number

  constructor(status: number) {
    super(`Auth request failed with HTTP ${status}`)
    this.status = status
  }
}

async function request(endpoint: 'session' | 'login' | 'logout', options?: RequestInit) {
  const headers = new Headers(options?.headers)
  headers.set('Accept', 'application/json')
  const response = await fetch(`/api/auth/${endpoint}`, {
    ...options,
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
    headers,
  })
  if (!response.ok) {
    throw new AuthHttpError(response.status)
  }
  return response
}

async function profileFrom(response: Response): Promise<SessionProfile> {
  const profile: unknown = await response.json()
  if (
    typeof profile === 'object' &&
    profile !== null &&
    'id' in profile &&
    typeof profile.id === 'number' &&
    'full_name' in profile &&
    typeof profile.full_name === 'string' &&
    'first_store_slug' in profile &&
    (profile.first_store_slug === null || typeof profile.first_store_slug === 'string')
  ) {
    return { id: profile.id, full_name: profile.full_name, first_store_slug: profile.first_store_slug }
  }
  throw new Error('Unexpected session response')
}

export async function fetchSession(signal: AbortSignal) {
  return profileFrom(await request('session', { signal }))
}

export async function createSession(login: string, password: string) {
  return profileFrom(
    await request('login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ login, password }),
    }),
  )
}

export async function removeSession() {
  const response = await request('logout', { method: 'POST' })
  if (response.status !== 204) {
    throw new Error('Unexpected logout response')
  }
}
