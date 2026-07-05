/**
 * src/services/http.ts
 *
 * Shared request-header helper for authenticated frontend API calls.
 */

function readCookie(name: string): string {
  const prefix = `${name}=`;
  const item = document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : '';
}

let currentSessionUserId = '';

type ApiErrorPayload = {
  detail?: unknown;
  message?: unknown;
  msg?: unknown;
};

function stringifyDetail(detail: unknown): string {
  if (typeof detail === 'string') {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => stringifyDetail(item))
      .filter(Boolean);
    return messages.join(' ');
  }
  if (detail && typeof detail === 'object') {
    const payload = detail as ApiErrorPayload;
    return (
      stringifyDetail(payload.message)
      || stringifyDetail(payload.msg)
      || stringifyDetail(payload.detail)
    );
  }
  return '';
}

export function apiErrorMessage(payload: unknown, fallback: string): string {
  const message = stringifyDetail(payload);
  return message || fallback;
}

export async function responseErrorMessage(response: Response): Promise<string> {
  const payload = await response.json().catch(() => ({}));
  return apiErrorMessage(payload, `HTTP ${response.status}`);
}

// Compose request headers, optionally including JSON content type and CSRF.
export function authHeaders(contentType = true): Record<string, string> {
  const csrfToken = readCookie('adaptiq_csrf');
  return {
    ...(contentType ? { 'Content-Type': 'application/json' } : {}),
    ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
  };
}

export function authFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers || {});
  const shouldSetJson = init.body !== undefined && !(init.body instanceof FormData);
  for (const [key, value] of Object.entries(authHeaders(shouldSetJson))) {
    if (!headers.has(key)) {
      headers.set(key, value);
    }
  }
  return fetch(input, {
    ...init,
    headers,
    credentials: 'include',
  });
}

export function setSessionUserId(userId: string): void {
  currentSessionUserId = userId;
}

export function getSessionUserId(): string {
  return currentSessionUserId;
}

export function clearSessionAuthData(): void {
  currentSessionUserId = '';
  sessionStorage.removeItem('adaptiq_classic_session_id');
  sessionStorage.removeItem('adaptiq_session_id');
}
