/**
 * Classic Room and dashboard API helpers backed by the FastAPI service.
 *
 * Covers:
 * - Classic session id lifecycle in sessionStorage.
 * - Question, hint, and answer calls for /api/rooms/classic.
 * - Dashboard stats/trend loading and lightweight health probing.
 */

import { DailyTrendPoint, Question, TopicType, UserStats } from '../types';
import { API_BASE } from '../config';
import { apiErrorMessage, authFetch, getSessionUserId, responseErrorMessage } from './http';
import { notifyDashboardStatsUpdated } from './dashboardEvents';


// ── Session tracking (persisted per browser session) ─────────────────────
const CLASSIC_SESSION_KEY = 'adaptiq_classic_session_id';

// Read current classic session id from session storage.
const getSessionId = (): string | null => {
  return sessionStorage.getItem(CLASSIC_SESSION_KEY);
};

// Persist classic session id for subsequent requests.
const setSessionId = (sessionId: string): void => {
  sessionStorage.setItem(CLASSIC_SESSION_KEY, sessionId);
};

const getUserId = (): string => {
  const id = getSessionUserId();
  if (!id) {
    throw new Error('Missing authenticated user session.');
  }
  return id;
};

// Map UI topic labels to backend enum values.
const toBackendTopic = (topic: TopicType): 'history' | 'geography' | 'mix' => {
  if (topic === 'History') return 'history';
  if (topic === 'Geography') return 'geography';
  return 'mix';
};

// Reset session ID at start of each quiz (new session per ClassicRoom run)
// Clear locally cached classic session binding.
export const resetSession = (): void => {
  sessionStorage.removeItem(CLASSIC_SESSION_KEY);
};


// ── POST /api/rooms/classic/questions ──────────────────────────────────
// Request the next classic question, creating/recovering session state as needed.
export const generateQuestion = async (
  topic: TopicType,
  difficulty: number,
  allowRetry = true,
): Promise<Question> => {
  const sessionId = getSessionId();
  const response = await authFetch(`${API_BASE}/api/rooms/classic/questions`, {
    method: 'POST',
    body: JSON.stringify({
      topic: toBackendTopic(topic),
      difficulty: Math.max(1, Math.min(5, Math.round(difficulty))),
      user_id: getUserId(),
      ...(sessionId ? { session_id: sessionId } : {}),
    }),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    const detail = apiErrorMessage(err, '');
    if (allowRetry && response.status === 404 && /session not found/i.test(detail)) {
      // Session in browser storage is stale; reset and start a fresh server session.
      resetSession();
      return generateQuestion(topic, difficulty, false);
    }
    throw new Error(detail || `HTTP ${response.status}`);
  }

  const data = await response.json();

  if (data?.session_id) {
    setSessionId(data.session_id);
  }

  // Backend intentionally hides correctAnswer until answer submission.
  if (!data.id || !data.text || !Array.isArray(data.options)) {
    throw new Error('Invalid question format from API');
  }

  return {
    ...data,
    correctAnswer: data.correctAnswer ?? '',
    explanation: data.explanation ?? '',
  } as Question;
};


// ── POST /api/rooms/classic/hints ──────────────────────────────────────
// Request a contextual hint for the active classic question.
export const generateHint = async (
  questionText: string,
  questionId?: string,
): Promise<string> => {
  const response = await authFetch(`${API_BASE}/api/rooms/classic/hints`, {
    method: 'POST',
    body: JSON.stringify({
      question_id: questionId || crypto.randomUUID(),
      question_text: questionText,
    }),
  });

  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }

  const data = await response.json();
  return data.hint as string;
};


// POST /api/rooms/classic/answers
export interface SubmitAnswerParams {
  question_id: string;
  selected_answer?: string;
  selected_index?: number;
  time_taken: number;      // seconds taken to answer
  used_hint: boolean;
}

export interface SubmitAnswerResult {
  success: boolean;
  updated_difficulty: number;
  is_correct: boolean;
  correct_answer: string;
  explanation: string;
  next_question?: Question;
}

// Submit one classic answer and normalize backend response shape.
export const submitAnswer = async (
  params: SubmitAnswerParams,
): Promise<SubmitAnswerResult> => {
  const sessionId = getSessionId();
  if (!sessionId) {
    return {
      success: false,
      updated_difficulty: 2,
      is_correct: false,
      correct_answer: '',
      explanation: '',
    };
  }

  const response = await authFetch(`${API_BASE}/api/rooms/classic/answers`, {
    method: 'POST',
    body: JSON.stringify({
      user_id: getUserId(),
      session_id: sessionId,
      question_id: params.question_id,
      ...(params.selected_answer !== undefined ? { selected_answer: params.selected_answer } : {}),
      ...(params.selected_index !== undefined ? { selected_index: params.selected_index } : {}),
      time_taken: params.time_taken,
      used_hint: params.used_hint,
    }),
  });

  if (!response.ok) {
    // Non-fatal — don't block the UI
    console.warn('submit-answer failed:', response.status);
    return {
      success: false,
      updated_difficulty: 2,
      is_correct: false,
      correct_answer: '',
      explanation: '',
    };
  }

  const data = await response.json();
  notifyDashboardStatsUpdated();
  const nextRaw = data.next_question;
  const nextQuestion = nextRaw && nextRaw.id && nextRaw.text && Array.isArray(nextRaw.options)
    ? {
        id: String(nextRaw.id),
        text: String(nextRaw.text),
        options: nextRaw.options as string[],
        correctAnswer: '',
        explanation: String(nextRaw.explanation ?? ''),
      } as Question
    : undefined;

  return {
    success: !!data.success,
    updated_difficulty: Number(data.new_difficulty ?? 2),
    is_correct: !!data.is_correct,
    correct_answer: String(data.correct_answer ?? ''),
    explanation: String(data.explanation ?? ''),
    next_question: nextQuestion,
  };
};


// ── Health check utility ──────────────────────────────────────────────────
// Probe backend health endpoint for quick connectivity checks.
export const checkApiHealth = async (): Promise<boolean> => {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
};


interface DashboardStatsApiResponse {
  id: string;
  points: number;
  level: string;
  total_questions: number;
  global_accuracy: number;
  daily_questions: number;
  daily_accuracy: number;
  learning_time_minutes: number;
  daily_points: number;
  streak_days: number;
  room_progress?: {
    classic?: number;
    challenge?: number;
    pvp?: number;
    custom?: number;
    visual?: number;
  };
  room_locks?: {
    classic?: boolean;
    challenge?: boolean;
    pvp?: boolean;
    custom?: boolean;
    visual?: boolean;
  };
}


interface DashboardTrendApiResponse {
  days: number;
  points: DailyTrendPoint[];
}


const parseApiError = async (response: Response): Promise<string> => {
  return responseErrorMessage(response);
};


// Load dynamic dashboard aggregate stats for the authenticated user.
export const fetchDashboardStats = async (): Promise<UserStats> => {
  const response = await authFetch(`${API_BASE}/api/auth/stats`);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  const data = (await response.json()) as DashboardStatsApiResponse;
  return {
    id: String(data.id ?? ''),
    points: Number(data.points ?? 0),
    level: String(data.level ?? 'Novice'),
    totalQuestions: Number(data.total_questions ?? 0),
    globalAccuracy: Number(data.global_accuracy ?? 0),
    dailyQuestions: Number(data.daily_questions ?? 0),
    dailyAccuracy: Number(data.daily_accuracy ?? 0),
    learningTimeMinutes: Number(data.learning_time_minutes ?? 0),
    dailyPoints: Number(data.daily_points ?? 0),
    streakDays: Number(data.streak_days ?? 0),
    roomProgress: {
      classic: Number(data.room_progress?.classic ?? 0),
      challenge: Number(data.room_progress?.challenge ?? 0),
      pvp: Number(data.room_progress?.pvp ?? 0),
      custom: Number(data.room_progress?.custom ?? 0),
      visual: Number(data.room_progress?.visual ?? 0),
    },
    roomLocks: {
      classic: Boolean(data.room_locks?.classic ?? false),
      challenge: Boolean(data.room_locks?.challenge ?? false),
      pvp: Boolean(data.room_locks?.pvp ?? false),
      custom: Boolean(data.room_locks?.custom ?? false),
      visual: Boolean(data.room_locks?.visual ?? false),
    },
  };
};


// Load day-by-day dashboard trend data for chart rendering.
export const fetchDashboardDailyTrend = async (days = 7): Promise<DashboardTrendApiResponse> => {
  const safeDays = Math.max(1, Math.min(90, Math.round(days)));
  const response = await authFetch(`${API_BASE}/api/auth/stats/daily-trend?days=${safeDays}`);
  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }
  const data = (await response.json()) as DashboardTrendApiResponse;
  return {
    days: Number(data.days ?? safeDays),
    points: Array.isArray(data.points) ? data.points : [],
  };
};
