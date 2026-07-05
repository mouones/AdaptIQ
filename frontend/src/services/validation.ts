/**
 * src/services/validation.ts
 *
 * Frontend form-validation helpers for auth flows.
 */

export interface ValidationResult {
  valid: boolean;
  error?: string;
}

// Validate password strength against security policy requirements.
export const validatePassword = (password: string): ValidationResult => {
  if (password.length < 8) {
    return { valid: false, error: 'Password must be at least 8 characters.' };
  }
  if (!/[A-Z]/.test(password)) {
    return { valid: false, error: 'Password must include an uppercase letter.' };
  }
  if (!/[a-z]/.test(password)) {
    return { valid: false, error: 'Password must include a lowercase letter.' };
  }
  if (!/[0-9]/.test(password)) {
    return { valid: false, error: 'Password must include a number.' };
  }
  if (!/[!@#$%^&*()\-_=+\[\]{};:,.?/]/.test(password)) {
    return { valid: false, error: 'Password must include a special character.' };
  }
  return { valid: true };
};
