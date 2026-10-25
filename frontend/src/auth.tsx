import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "./api";

export type Officer = { id: string; email: string; full_name: string; designation?: string; role: string; mfa_enabled?: boolean };
type Me = { officer: Officer; mfa_pending: boolean; session: { expires_at: string; idle_timeout_minutes: number } } | null;

const Ctx = createContext<{ me: Me; loading: boolean; refresh: () => Promise<void>; logout: () => Promise<void>; setMe: (m: Me) => void }>(null as any);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me>(null);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try {
      setMe(await api("/api/auth/me", { quiet401: true }));
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    refresh();
    setUnauthorizedHandler(() => setMe(null));
  }, [refresh]);
  const logout = useCallback(async () => {
    try {
      await api("/api/auth/logout", { method: "POST", quiet401: true });
    } finally {
      setMe(null);
    }
  }, []);
  return <Ctx.Provider value={{ me, loading, refresh, logout, setMe }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
