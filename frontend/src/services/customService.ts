/**
 * src/services/customService.ts
 *
 * API client for Custom Room:
 *   - session start/generate/submit/end
 *   - topic and concept lookup
 *   - per-user concept mastery fetch
 */

import type {
  StartSessionResponse,
  GenerateQuestionRequest,
  CustomQuestion,
  CustomLevel,
  SubmitAnswerRequest,
  SubmitAnswerResponse,
  EndSessionResponse,
} from '../types/custom';
import { API_BASE } from '../config';
import { authFetch, getSessionUserId, responseErrorMessage } from './http';
import { notifyDashboardStatsUpdated } from './dashboardEvents';

const BASE_URL = `${API_BASE}/api/custom`;

// ─── Helper ───────────────────────────────────────────────────────────────────
// Resolve active user id from storage and enforce authenticated usage.
function getUserId(): string {
  const id = getSessionUserId();
  if (!id) {
    throw new Error('No authenticated user id found. Please login again.');
  }
  return id;
}

// Parse JSON payloads and surface backend detail errors.
async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }
  return res.json() as Promise<T>;
}

// ─── Endpoints ────────────────────────────────────────────────────────────────

/**
 * POST /api/custom/start-session
 * Creates (or resumes) a session for the chosen topic
 */
// Start a custom-room session for selected topic/concept context.
export async function startCustomSession(topic: string, concept_id?: string): Promise<StartSessionResponse> {
  const res = await authFetch(`${BASE_URL}/start-session`, {
    method: 'POST',
    body: JSON.stringify({ user_id: getUserId(), topic, concept_id }),
  });
  return handleResponse<StartSessionResponse>(res);
}

/**
 * POST /api/custom/generate-question
 * Generates a fresh question from the topic
 */
// Generate the next custom-room question with one retry on rate-limit.
export async function generateCustomQuestion(
  session_id: string,
  topic: string,
  concept_id?: string,
  level: CustomLevel = 3,
): Promise<CustomQuestion> {
  const payload: GenerateQuestionRequest = { session_id, topic, concept_id, level };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const res = await authFetch(`${BASE_URL}/generate-question`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    if (res.status === 429 && attempt === 0) {
      await new Promise((resolve) => setTimeout(resolve, 1200));
      continue;
    }

    return handleResponse<CustomQuestion>(res);
  }

  throw new Error('Unable to generate a question right now. Please retry.');
}

/**
 * POST /api/custom/submit-answer
 * Verifies the answer and updates progress
 */
// Submit one custom-room answer and return updated progress metadata.
export async function submitCustomAnswer(
  payload: SubmitAnswerRequest,
): Promise<SubmitAnswerResponse> {
  const res = await authFetch(`${BASE_URL}/submit-answer`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  const data = await handleResponse<SubmitAnswerResponse>(res);
  notifyDashboardStatsUpdated();
  return data;
}

/**
 * POST /api/custom/session/{session_id}/end
 * Marks the session as finished
 */
// End active custom-room session and return summary stats.
export async function endCustomSession(session_id: string): Promise<EndSessionResponse> {
  const res = await authFetch(`${BASE_URL}/session/${session_id}/end`, {
    method: 'POST',
  });
  return handleResponse<EndSessionResponse>(res);
}

/**
 * GET /api/custom/topics
 * Returns list of available topics
 */
export interface CustomTopic {
  type: string;
  slug: string;
  name: string;
  description: string;
  total_facts: number;
}

export interface CustomTopicsResponse {
  topics: CustomTopic[];
}

// Fetch available custom-room topics.
export async function getCustomTopics(): Promise<CustomTopicsResponse> {
  const res = await authFetch(`${BASE_URL}/topics`);
  return handleResponse<CustomTopicsResponse>(res);
}

// ─── Concept-related endpoints ────────

export interface ConceptOut {
  id: string;
  name: string;
  topic: string;
  scope?: string;
  description?: string;
}

// Fetch concepts for a topic family used by custom-room concept focus.
export async function getConceptsForTopic(topic: string): Promise<ConceptOut[]> {
  const topicFamily = topic.toLowerCase().startsWith('history')
    ? 'history'
    : topic.toLowerCase().startsWith('geography')
      ? 'geography'
      : 'mixed';
  const res = await authFetch(`${BASE_URL}/concepts/${topicFamily}`);
  const data = await handleResponse<{ concepts: ConceptOut[] }>(res);
  return data.concepts || [];
}

export interface ConceptMasteryItem {
  concept_id: string;
  concept: string;
  topic: string;
  scope?: string;
  theta: number;
  response_count: number;
  mastery_level: string;
  exposure_count: number;
}

export interface ConceptMasteryResponse {
  user_id: string;
  concepts: ConceptMasteryItem[];
}

// Fetch concept mastery list for the current or specified user.
export async function getUserConceptMastery(userId?: string): Promise<ConceptMasteryResponse> {
  const uid = userId ?? getUserId();
  const res = await authFetch(`${BASE_URL}/user/${uid}/concept-mastery`);
  return handleResponse<ConceptMasteryResponse>(res);
}
