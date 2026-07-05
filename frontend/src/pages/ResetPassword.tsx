/**
 * src/pages/ResetPassword.tsx
 *
 * OTP/code-based password reset screen with password policy enforcement.
 */

import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { validatePassword } from '../services/validation';
import { API_BASE } from '../config';
import { responseErrorMessage } from '../services/http';
import '../styles/auth.css';

// Render reset-password form and submit OTP + new password payload.
export default function ResetPassword() {
  const navigate = useNavigate();
  const location = useLocation();
  const prefillEmail = (location.state as { email?: string } | null)?.email ?? '';

  const [email, setEmail] = useState(prefillEmail);
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  // Validate fields and send reset-password request.
  const handleSubmit = async () => {
    if (!email.trim() || !code.trim() || !newPassword) {
      setError('Please fill in all fields.');
      return;
    }

    const passwordValidation = validatePassword(newPassword);
    if (!passwordValidation.valid) {
      setError(passwordValidation.error || 'Invalid password.');
      return;
    }

    setError('');
    setIsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          code: code.trim(),
          new_password: newPassword,
        }),
      });
      if (!res.ok) {
        throw new Error(await responseErrorMessage(res));
      }
      setDone(true);
    } catch (err: any) {
      setError(err.message ?? 'Reset failed. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-bg"></div>
      <div className="auth-card">
        <h2 className="auth-title">Reset Password</h2>

        {done ? (
          <div style={{ textAlign: 'center' }}>
            <p style={{ color: '#2D1B14', marginBottom: 16 }}>
              Your password has been reset successfully.
            </p>
            <button className="auth-button" onClick={() => navigate('/login')}>
              Go to Login
            </button>
          </div>
        ) : (
          <>
            {error && (
              <div style={{ color: '#c0392b', fontSize: 13, marginBottom: 10, textAlign: 'center', background: 'rgba(192,57,43,0.07)', borderRadius: 8, padding: '8px 12px' }}>
                {error}
              </div>
            )}

            <input
              className="auth-input"
              placeholder="Email"
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              disabled={isLoading}
            />
            <input
              className="auth-input"
              placeholder="6-digit reset code"
              type="text"
              inputMode="numeric"
              maxLength={8}
              value={code}
              onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
              disabled={isLoading}
            />
            <input
              className="auth-input"
              placeholder="New password (8+ chars, A-Z a-z 0-9 !@#...)"
              type="password"
              value={newPassword}
              onChange={e => setNewPassword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSubmit()}
              disabled={isLoading}
            />

            <button className="auth-button" onClick={handleSubmit} disabled={isLoading}>
              {isLoading ? 'Resetting...' : 'Reset Password'}
            </button>

            <div className="auth-switch">
              <span className="auth-link" onClick={() => navigate('/forgot-password')}>
                Resend Code
              </span>
              {' · '}
              <span className="auth-link" onClick={() => navigate('/login')}>
                Back to Login
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
