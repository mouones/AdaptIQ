/**
 * src/pages/Login.tsx
 *
 * Login screen handling credential submission and auth context hydration.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import "../styles/auth.css";
import "../styles/login.css";
import { API_BASE } from "../config";
import { useAuth } from "../context/AuthContext";
import { responseErrorMessage } from "../services/http";

// Render login form and authenticate user against backend auth endpoint.
export default function Login() {
  const navigate = useNavigate();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  // Validate credentials, call login API, and persist auth state.
  const handleLogin = async () => {
    setError("");
    if (!email.trim() || !password) {
      setError("Please enter email and password.");
      return;
    }

    setIsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
      });

      if (!res.ok) {
        throw new Error(await responseErrorMessage(res));
      }
      const data = await res.json().catch(() => ({}));

      if (!data.access_token || !data.user) {
        throw new Error("Invalid authentication response.");
      }
      login(data.access_token, data.user);
      navigate("/dashboard");
    } catch (err: any) {
      setError(err.message ?? "Login failed.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-bg"></div>
      <div className="auth-card">
        <h2 className="auth-title">Log In</h2>

        {error && (
          <div style={{ color: '#c0392b', fontSize: 13, marginBottom: 10, textAlign: 'center', background: 'rgba(192,57,43,0.07)', borderRadius: 8, padding: '8px 12px', width: '100%' }}>
            {error}
          </div>
        )}

        <input className="auth-input" placeholder="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} disabled={isLoading} />
        <input className="auth-input" placeholder="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && handleLogin()} disabled={isLoading} />

        <button className="auth-button" onClick={handleLogin} disabled={isLoading}>
          {isLoading ? "Logging In..." : "Log In"}
        </button>

        <div className="auth-switch" style={{ marginTop: 10 }}>
          <span className="auth-link" onClick={() => navigate('/forgot-password')}>
            Forgot password?
          </span>
        </div>

        <div className="auth-switch">
          Don’t have an account?{" "}
          <span className="auth-link" onClick={() => navigate("/signup")}>
            Sign up
          </span>
        </div>
      </div>
    </div>
  );
}
