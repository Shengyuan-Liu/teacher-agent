import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AuthState {
  token: string | null
  refreshToken: string | null
  email: string | null
  setAuth: (token: string, refreshToken: string, email: string) => void
  setToken: (token: string, refreshToken: string) => void
  clear: () => void
}

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      refreshToken: null,
      email: null,
      setAuth: (token, refreshToken, email) => set({ token, refreshToken, email }),
      setToken: (token, refreshToken) => set({ token, refreshToken }),
      clear: () => set({ token: null, refreshToken: null, email: null }),
    }),
    { name: 'teacher-agent-auth' },
  ),
)
