/** Backend API base URL for frontend service clients. */

export const API_BASE = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000';

/** Per-question countdown (mirrors backend QUIZ_TIME_LIMIT_SECONDS). */
export const TIMER_SECONDS = 30;
