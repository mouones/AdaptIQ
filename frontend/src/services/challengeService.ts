/**
 * Challenge Room API client and response normalization helpers.
 */

import {
  UserRank,
  ChallengeLevel,
  ChallengeQuestion,
  ChallengeSessionState,
  StartSessionResponse,
  SubmitAnswerResponse,
  EndSessionResponse,
  TopicType,
} from '../types/challenge';
import { API_BASE } from '../config';
import { authFetch, getSessionUserId, responseErrorMessage } from './http';
import { notifyDashboardStatsUpdated } from './dashboardEvents';

// ── Config ───────────────────────────────────────────────────────────────

// Auth helpers.

/**
 * Get the logged-in user's ID from in-memory browser session state.
 */
const getUserId = (): string => {
  const id = getSessionUserId();
  if (!id) {
    throw new Error('Missing authenticated user session.');
  }
  return id;
};

// ── Error helper ─────────────────────────────────────────────────────────

// Parse successful JSON responses or throw backend detail errors.
async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }
  return res.json() as Promise<T>;
}

// ── Prefetch cache helpers ───────────────────────────────────────────────

const prefetchKey = (sessionId: string): string => `challenge_prefetch_${sessionId}`;

/** Remove all cached prefetched questions for a session. */
export function clearChallengePrefetch(sessionId: string): void {
  try {
    sessionStorage.removeItem(prefetchKey(sessionId));
  } catch {
    // ignore storage failures
  }
}

/** Read cached questions, keeping only those matching the expected level. */
function readPrefetchCache(sessionId: string, expectedLevel: ChallengeLevel): ChallengeQuestion[] {
  try {
    const raw = JSON.parse(sessionStorage.getItem(prefetchKey(sessionId)) || '[]') as ChallengeQuestion[];
    if (!Array.isArray(raw)) return [];
    return raw.filter((q) => q && q.level === expectedLevel);
  } catch {
    return [];
  }
}

function writePrefetchCache(sessionId: string, questions: ChallengeQuestion[]): void {
  try {
    sessionStorage.setItem(prefetchKey(sessionId), JSON.stringify(questions));
  } catch {
    // ignore storage failures
  }
}

/**
 * Pop the next prefetched question only if it matches the session's current level.
 * Stale entries from a prior level (e.g. after derank) are discarded.
 */
export function takePrefetchedQuestion(
  sessionId: string,
  expectedLevel: ChallengeLevel,
): ChallengeQuestion | undefined {
  const cached = readPrefetchCache(sessionId, expectedLevel);
  if (!cached.length) {
    clearChallengePrefetch(sessionId);
    return undefined;
  }
  const [next, ...rest] = cached;
  writePrefetchCache(sessionId, rest);
  return next;
}

/**
 * Prefetch used to generate multiple questions in parallel and could exhaust the LLM
 * quota or cache a stale level after a derank. Keep this as a safe no-op so older
 * callers do not break, while the quiz loads exactly one question on demand.
 */
export function prefetchChallengeQuestions(
  _topic: TopicType,
  _level: ChallengeLevel,
  _sessionId: string,
  _count = 1,
): void {
  return;
}


// ═════════════════════════════════════════════════════════════════════════
// GET /api/challenge/user/{user_id}/rank
// ═════════════════════════════════════════════════════════════════════════

/**
 * Fetch the user's current rank, points, and available starting levels.
 * Called on the selection screen to show rank badge and lock/unlock cards.
 */
// Load challenge rank snapshot for the current authenticated user.
export async function getUserRank(): Promise<UserRank> {
  const userId = getUserId();
  const res = await authFetch(`${API_BASE}/api/challenge/user/${userId}/rank`);
  const data = await handleResponse<{
    current_rank    : string;
    rank_points     : number;
    available_levels: number[];
    total_sessions  : number;
    total_questions : number;
  }>(res);

  // Map backend shape → frontend UserRank (add convenience aliases)
  return {
    current_rank    : data.current_rank     as UserRank['current_rank'],
    rank_points     : data.rank_points,
    available_levels: data.available_levels as ChallengeLevel[],
    total_sessions  : data.total_sessions,
    total_questions : data.total_questions,
    // convenience aliases used in ChallengeRoom.tsx JSX
    level_access    : data.available_levels as ChallengeLevel[],
    total_points    : data.rank_points,
  };
}


// ═════════════════════════════════════════════════════════════════════════
// POST /api/challenge/start-session  →  ChallengeSessionState
// ═════════════════════════════════════════════════════════════════════════

/**
 * Create a new challenge session and load the first question.
 * Returns the full initial ChallengeSessionState used by React useState.
 */
// Start a new challenge session and hydrate initial client state.
export async function startChallengeSession(
  topic        : TopicType,
  startingLevel: ChallengeLevel,
): Promise<ChallengeSessionState> {
  const userId = getUserId();

  // 1. Create session
  const sessionRes = await authFetch(`${API_BASE}/api/challenge/start-session`, {
    method : 'POST',
    body   : JSON.stringify({
      user_id       : userId,
      topic,
      starting_level: startingLevel,
    }),
  });
  const sessionData = await handleResponse<StartSessionResponse>(sessionRes);

  // 2. Load first question immediately
  const firstQuestion = await generateChallengeQuestion(
    topic,
    sessionData.current_level as ChallengeLevel,
    sessionData.session_id,
  );

  clearChallengePrefetch(sessionData.session_id);

  // 3. Build initial session state
  return {
    session_id    : sessionData.session_id,
    topic,
    current_level : sessionData.current_level as ChallengeLevel,
    current_rank  : sessionData.current_rank,
    rank_points   : 0,
    streak_correct: 0,
    streak_wrong  : 0,
    questions     : [firstQuestion],
    currentIndex  : 0,
    score         : 0,
    pointsEarned  : 0,
    force_level_change: null,
  };
}


// ═════════════════════════════════════════════════════════════════════════
// POST /api/challenge/generate-question
// ═════════════════════════════════════════════════════════════════════════

/**
 * Generate the next challenge question at the current level.
 * Called after each correct answer or forced level change.
 */
// Request one challenge question for the active session and level.
export async function generateChallengeQuestion(
  topic    : TopicType,
  level    : ChallengeLevel,
  sessionId: string,
): Promise<ChallengeQuestion> {
  const userId = getUserId();
  const res = await authFetch(`${API_BASE}/api/challenge/generate-question`, {
    method : 'POST',
    body   : JSON.stringify({
      session_id: sessionId,
      user_id   : userId,
      topic,
      level,
    }),
  });
  return handleResponse<ChallengeQuestion>(res);
}


// ═════════════════════════════════════════════════════════════════════════
// POST /api/challenge/submit-answer
// ═════════════════════════════════════════════════════════════════════════

/**
 * Submit the user's answer for the current question.
 * Returns whether it's correct, points change, new level, and streak state.
 * If force_level_change is present, the frontend should show the popup.
 */
// Submit one challenge answer and return scoring/progression updates.
export async function submitChallengeAnswer(
  session       : ChallengeSessionState,
  selectedAnswer: string,
  timeTaken?: number,
): Promise<SubmitAnswerResponse> {
  const userId = getUserId();
  const currentQuestion = session.questions[session.currentIndex];

  const res = await authFetch(`${API_BASE}/api/challenge/submit-answer`, {
    method : 'POST',
    body   : JSON.stringify({
      session_id : session.session_id,
      question_id: currentQuestion.id,
      user_id    : userId,
      answer     : selectedAnswer,
      time_taken : typeof timeTaken === 'number' ? Math.max(0, Math.round(timeTaken)) : null,
    }),
  });
  const data = await handleResponse<SubmitAnswerResponse>(res);
  notifyDashboardStatsUpdated();
  return data;
}


// ═════════════════════════════════════════════════════════════════════════
// POST /api/challenge/session/{session_id}/end
// ═════════════════════════════════════════════════════════════════════════

/**
 * End the session after 10 questions.
 * Updates global rank and returns the summary data.
 */
// Finalize challenge session and fetch rank-impact summary.
export async function endChallengeSession(
  sessionId: string,
): Promise<EndSessionResponse> {
  const res = await authFetch(`${API_BASE}/api/challenge/session/${sessionId}/end`, {
    method : 'POST',
  });
  return handleResponse<EndSessionResponse>(res);
}


// ═════════════════════════════════════════════════════════════════════════
// (Optional) GET /api/challenge/session/{session_id}
// ═════════════════════════════════════════════════════════════════════════

/**
 * Fetch raw session state from the backend (useful for reconnect/reload).
 * Not currently used by ChallengeRoom.tsx — state is held in React.
 */
// Read raw challenge session state by id (optional reconnect helper).
export async function getChallengeSession(sessionId: string) {
  const res = await authFetch(`${API_BASE}/api/challenge/session/${sessionId}`);
  return handleResponse(res);
}
