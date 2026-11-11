/** Thin fetch wrapper for the Docs Health FastAPI backend (same-origin /api, proxied). */
import { useCallback, useEffect, useState } from 'react'

const TOKEN_KEY = 'dh_token'

export const tokenStore = {
  get() {
    try {
      return sessionStorage.getItem(TOKEN_KEY)
    } catch {
      return null
    }
  },
  set(token) {
    try {
      if (token) sessionStorage.setItem(TOKEN_KEY, token)
      else sessionStorage.removeItem(TOKEN_KEY)
    } catch {
      /* storage unavailable */
    }
  },
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

let onUnauthorized = () => {}
export const setUnauthorizedHandler = (fn) => {
  onUnauthorized = fn
}

export async function api(path, { method, body, form } = {}) {
  const headers = {}
  const token = tokenStore.get()
  if (token) headers.Authorization = `Bearer ${token}`
  let payload
  if (form) payload = form
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  let res
  try {
    res = await fetch(`/api${path}`, { method: method || (payload ? 'POST' : 'GET'), headers, body: payload })
  } catch {
    throw new ApiError(0, 'Cannot reach the Docs Health server. Check your connection and try again.')
  }
  // A signed-in session was rejected (expired, or revoked by a password change): sign out.
  if (res.status === 401 && token && !path.startsWith('/auth/login')) onUnauthorized()
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }
  if (!res.ok) {
    const detail = data?.detail
    const msg =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join('; ')
          : res.status === 502 || res.status === 504
            ? 'The Docs Health API is not reachable.'
            : res.status >= 500
              ? 'The server hit an error. Please try again.'
              : res.statusText || `Request failed (${res.status})`
    throw new ApiError(res.status, msg)
  }
  return data
}

export const post = (path, body = {}) => api(path, { method: 'POST', body })
export const patch = (path, body) => api(path, { method: 'PATCH', body })

/** Load data from the API with reload support. */
export function useApi(path, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    if (!path) return
    setLoading(true)
    try {
      setData(await api(path))
      setError(null)
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [path])
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, ...deps])
  return { data, setData, error, loading, reload: load }
}

/** Run an async action with busy/error state. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const run = async (fn) => {
    setBusy(true)
    setError(null)
    try {
      return await fn()
    } catch (e) {
      setError(e)
      return undefined
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run, setError }
}
