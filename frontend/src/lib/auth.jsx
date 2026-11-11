import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, post, setUnauthorizedHandler, tokenStore } from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [ready, setReady] = useState(false)

  const logout = useCallback(() => {
    tokenStore.set(null)
    setUser(null)
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(logout)
    if (!tokenStore.get()) {
      setReady(true)
      return
    }
    api('/auth/me')
      .then(setUser)
      .catch(logout)
      .finally(() => setReady(true))
  }, [logout])

  const login = async (email, password) => {
    const r = await post('/auth/login', { email, password })
    tokenStore.set(r.access_token)
    setUser(r.user)
  }

  return <AuthContext.Provider value={{ user, ready, login, logout }}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)
