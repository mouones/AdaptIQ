import type { Page, Route } from '@playwright/test';

const API_HOST_PATTERN = 'https?://(?:127\\.0\\.0\\.1|localhost):8000';

export type TestUser = {
  id: string;
  email: string;
  username: string;
  points: number;
  level: string;
  is_active: boolean;
  is_admin: boolean;
  created_at?: string;
};

export const normalUser: TestUser = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'scholar@adaptiq.dev',
  username: 'scholar_user',
  points: 1250,
  level: 'Scholar',
  is_active: true,
  is_admin: false,
  created_at: '2026-01-15T00:00:00Z',
};

export function apiPattern(pathPattern: string): RegExp {
  return new RegExp(`^${API_HOST_PATTERN}${pathPattern}$`);
}

export async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

export async function mockApiJson(
  page: Page,
  pathPattern: string,
  body: unknown,
  status = 200,
): Promise<void> {
  await page.route(apiPattern(pathPattern), async (route) => {
    await fulfillJson(route, body, status);
  });
}

export async function mockAuthenticatedUser(page: Page, user: TestUser = normalUser): Promise<void> {
  await mockApiJson(page, '/api/auth/me', { user });
  await mockApiJson(page, '/api/auth/logout', { success: true });
}

export async function mockUnauthenticated(page: Page): Promise<void> {
  await mockApiJson(page, '/api/auth/me', { detail: 'Not authenticated' }, 401);
  await mockApiJson(page, '/api/auth/logout', { success: true });
}

export async function mockDashboardApis(page: Page, user: TestUser = normalUser): Promise<void> {
  await mockApiJson(page, '/api/auth/stats', {
    id: user.id,
    points: user.points,
    level: user.level,
    total_questions: 48,
    global_accuracy: 78,
    daily_questions: 6,
    daily_accuracy: 83,
    learning_time_minutes: 18,
    daily_points: 42,
    streak_days: 4,
    room_progress: {
      classic: 35,
      challenge: 12,
      pvp: 0,
      custom: 20,
      visual: 5,
    },
    room_locks: {
      classic: false,
      challenge: false,
      pvp: false,
      custom: false,
      visual: false,
    },
  });

  await mockApiJson(page, '/api/auth/stats/daily-trend\\?days=7', {
    days: 7,
    points: [
      { day: 'Mon', count: 1 },
      { day: 'Tue', count: 3 },
      { day: 'Wed', count: 0 },
      { day: 'Thu', count: 5 },
      { day: 'Fri', count: 2 },
      { day: 'Sat', count: 4 },
      { day: 'Sun', count: 6 },
    ],
  });

  await mockApiJson(page, `/api/onboarding/status\\?user_id=${user.id}`, {
    first_login: false,
    onboarding_needed: false,
    onboarding_completed: true,
    tour_needed: false,
  });
}
