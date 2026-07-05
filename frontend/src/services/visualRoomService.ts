/**
 * Visual Room API client.
 *
 * Correct answers are never included in the question payload from /next.
 * Hints and explanations are fetched through separate authenticated endpoints.
 */

import type { TopicType } from '../types';
import type {
  VisualQuestion,
  StartVisualSessionResponse,
  SubmitVisualAnswerResponse,
  VisualEndSessionResponse,
} from '../types/visual';
import { notifyDashboardStatsUpdated } from './dashboardEvents';
import { API_BASE } from '../config';
import { authFetch, getSessionUserId } from './http';

// ── Auth helpers (match other rooms) ─────────────────────────────────────────

const getUserId = (): string => {
  const id = getSessionUserId();
  if (!id) {
    throw new Error('Missing authenticated user session.');
  }
  return id;
};

// ─── Types ────────────────────────────────────────────────────────────────────

export type { StartVisualSessionResponse, SubmitVisualAnswerResponse, VisualQuestion, VisualEndSessionResponse };

// ─── Start session ────────────────────────────────────────────────────────────

export async function startVisualSession(
  topic: TopicType,
  level: number
): Promise<StartVisualSessionResponse> {
  const userId = getUserId();
  const res = await authFetch(`${API_BASE}/api/visual/start-session`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, topic, level }),
  });
  if (!res.ok) throw new Error(`start-session failed: ${res.status}`);
  return res.json();
}

// ─── Fetch next question (no correct answer in response) ─────────────────────

export async function fetchNextVisualQuestion(
  sessionId: string,
): Promise<VisualQuestion> {
  const params = new URLSearchParams({
    session_id: sessionId,
  });
  const res = await authFetch(`${API_BASE}/api/visual/next?${params}`);
  if (!res.ok) throw new Error(`fetch next failed: ${res.status}`);
  return res.json();
}

// ─── Submit answer ────────────────────────────────────────────────────────────

export async function submitVisualAnswer(
  sessionId: string,
  questionId: string,
  chosenAnswer: string,
  userTimeMs?: number
): Promise<SubmitVisualAnswerResponse> {
  const userId = getUserId();
  const res = await authFetch(`${API_BASE}/api/visual/submit`, {
    method: 'POST',
    body: JSON.stringify({
      session_id:    sessionId,
      question_id:   questionId,
      user_id:       userId,
      chosen_answer: chosenAnswer,
      user_time_ms:  userTimeMs,
    }),
  });
  if (!res.ok) throw new Error(`submit failed: ${res.status}`);
  const data = await res.json();
  notifyDashboardStatsUpdated();
  return data;
}

// ─── Hint (uses question_id only — correct answer never sent to frontend) ─────

export async function fetchVisualHint(questionId: string, sessionId: string): Promise<string> {
  const params = new URLSearchParams({ question_id: questionId, session_id: sessionId });
  const res = await authFetch(`${API_BASE}/api/visual/hint?${params}`);
  if (!res.ok) throw new Error(`hint fetch failed: ${res.status}`);
  const data = await res.json();
  return data.hint as string;
}

// ─── End session ──────────────────────────────────────────────────────────────

export async function endVisualSession(sessionId: string) {
  const res = await authFetch(`${API_BASE}/api/visual/session/${sessionId}/end`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`end session failed: ${res.status}`);
  return res.json() as Promise<VisualEndSessionResponse>;
}
