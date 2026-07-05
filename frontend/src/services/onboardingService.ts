// src/services/onboardingService.ts
// All API calls for the onboarding flow.
// Covers status retrieval, survey submit/skip, and guided-tour completion.

import { API_BASE } from '../config';
import { authFetch, responseErrorMessage } from './http';

const BASE_URL = `${API_BASE}/api/onboarding`;

// Parse onboarding API response and throw normalized errors.
async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }
  return res.json() as Promise<T>;
}

// Helper for POSTing onboarding payloads to the backend.
function post(path: string, body: object) {
  return authFetch(`${BASE_URL}${path}`, {
    method:  'POST',
    body:    JSON.stringify(body),
  });
}

export interface OnboardingStatus {
  first_login:           boolean;
  onboarding_needed:     boolean;
  onboarding_completed:  boolean;
  tour_needed:           boolean;
}

/**
 * Called once when the Dashboard mounts.
 * Creates the onboarding flags row if it doesn't exist yet.
 */
// Load onboarding status flags for the authenticated user.
export async function getOnboardingStatus(userId: string): Promise<OnboardingStatus> {
  const res = await authFetch(`${BASE_URL}/status?user_id=${userId}`);
  return handleResponse<OnboardingStatus>(res);
}

/**
 * Called when user clicks "I'm ready to start learning" in OnboardingModal.
 */
// Submit onboarding survey selections.
export async function submitOnboardingSurvey(
  userId: string,
  topicsConfident:    string[],
  topicsWantToLearn:  string[],
): Promise<{ success: boolean; redirect_to_dashboard: boolean }> {
  const res = await post('/survey', {
    user_id:              userId,
    topics_confident:     topicsConfident,
    topics_want_to_learn: topicsWantToLearn,
  });
  return handleResponse(res);
}

/**
 * Called when user clicks "Skip Onboarding" in OnboardingModal.
 */
// Skip onboarding survey while marking onboarding flow complete.
export async function skipOnboarding(userId: string): Promise<{ success: boolean }> {
  const res = await post('/skip', { user_id: userId });
  return handleResponse(res);
}

/**
 * Called when user finishes or skips the GuidedTour.
 */
// Mark guided tour as completed for this user.
export async function markTourSeen(userId: string): Promise<{ success: boolean }> {
  const res = await post('/mark-tour-seen', { user_id: userId });
  return handleResponse(res);
}
