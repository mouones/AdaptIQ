/** In-memory browser auth state, refreshed from the backend cookie session. */

import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { API_BASE } from '../config';
import { authFetch, clearSessionAuthData, setSessionUserId } from '../services/http';

interface AuthUser {
  id: string;
  email: string;
  username: string;
  points: number;
  level: string;
  is_active: boolean;
  is_admin: boolean;
  created_at?: string;
}

interface AuthContextType {
  user: AuthUser | null;
  isLoading: boolean;
  login: (token: string, user: AuthUser) => void;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const PUBLIC_ROUTES = new Set(['/', '/login', '/signup', '/forgot-password', '/reset-password']);

function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTES.has(pathname);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const didBootstrapRef = useRef(false);
  const refreshInFlightRef = useRef(false);
  const lastRefreshRef = useRef<number>(Date.now());
  const hasActivityRef = useRef<boolean>(false);

  const login = (token: string, userData: AuthUser) => {
    void token;
    setSessionUserId(userData.id);
    setUser(userData);
    lastRefreshRef.current = Date.now();
  };

  const logout = () => {
    void authFetch(`${API_BASE}/api/auth/logout`, { method: 'POST' }).catch(() => undefined);
    clearSessionAuthData();
    setUser(null);
  };

  const refreshUser = async () => {
    if (refreshInFlightRef.current) {
      return;
    }

    refreshInFlightRef.current = true;
    try {
      const res = await authFetch(`${API_BASE}/api/auth/me`);

      if (!res.ok) {
        logout();
        return;
      }

      const data = await res.json().catch(() => ({}));
      if (data?.user) {
        setUser(data.user as AuthUser);
        setSessionUserId(data.user.id);
        lastRefreshRef.current = Date.now();
      }
    } catch {
      logout();
    } finally {
      refreshInFlightRef.current = false;
    }
  };

  useEffect(() => {
    if (didBootstrapRef.current) {
      return;
    }
    didBootstrapRef.current = true;

    if (isPublicRoute(window.location.pathname)) {
      setIsLoading(false);
      return;
    }

    const bootstrap = async () => {
      try {
        await refreshUser();
      } finally {
        setIsLoading(false);
      }
    };
    bootstrap();
  }, []);

  // Listen to user activity and refresh session periodically
  useEffect(() => {
    if (!user) return;

    const handleActivity = () => {
      hasActivityRef.current = true;
    };

    const activityEvents = ['mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart'];
    activityEvents.forEach(evt => {
      window.addEventListener(evt, handleActivity, { passive: true });
    });

    const intervalId = setInterval(() => {
      const now = Date.now();
      // 10 minutes interval for active session extension
      const tenMinutes = 10 * 60 * 1000;
      if (hasActivityRef.current && (now - lastRefreshRef.current >= tenMinutes)) {
        hasActivityRef.current = false;
        refreshUser();
      }
    }, 60000); // Check every minute

    return () => {
      activityEvents.forEach(evt => {
        window.removeEventListener(evt, handleActivity);
      });
      clearInterval(intervalId);
    };
  }, [user]);

  const value = useMemo(
    () => ({ user, isLoading, login, logout, refreshUser }),
    [user, isLoading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}
