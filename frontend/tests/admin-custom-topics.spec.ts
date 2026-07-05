/** Playwright smoke coverage for admin custom topics.spec behavior. */

import { expect, test } from '@playwright/test';

const API_BASE = process.env.VITE_API_URL ?? 'http://localhost:8000';

test('admin dashboard approves a custom topic candidate', async ({ page }) => {
  let approved = false;
  let isTopicActive = true;
  const requests: unknown[] = [];

  await page.route(`${API_BASE}/api/auth/me`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user: {
          id: 'admin-test-id',
          email: 'admin@example.com',
          username: 'admin_user',
          points: 0,
          level: 'Expert',
          is_active: true,
          is_admin: true,
        },
      }),
    });
  });

  await page.route(`${API_BASE}/api/admin/overview`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        users: { total: 1, active: 1, admin: 1, banned: 0, latest_created_at: null },
        questions: { total: 12, latest_created_at: null },
        sessions: { classic: 0, challenge: 0, custom: 0, pvp: 0 },
        concepts: { total: 0, mastery_rows: 0 },
        responses: { total: 0 },
        pvp: { total_matches: 0, rated_players: 0 },
      }),
    });
  });

  await page.route(`${API_BASE}/api/admin/top-concepts`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [] }),
    });
  });

  await page.route(`${API_BASE}/api/admin/analytics/daily?days=14`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        days: 14,
        start_date: null,
        end_date: null,
        items: [],
        totals: {
          new_users: 0,
          responses: 0,
          correct: 0,
          classic_sessions: 0,
          challenge_sessions: 0,
          custom_sessions: 0,
          pvp_matches: 0,
        },
        top_users: [],
      }),
    });
  });

  await page.route(`${API_BASE}/api/admin/custom-topics/candidates?limit=100`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          {
            type: 'History',
            name: 'World War I',
            slug: 'ww1',
            description: 'Admin-approved custom room for World War I.',
            source_topic: 'history',
            available_question_count: 42,
            approved,
            is_active: isTopicActive,
            candidate_source: 'question_bank',
          },
        ],
        total: 1,
      }),
    });
  });

  await page.route(`${API_BASE}/api/admin/custom-topics/approve`, async (route) => {
    requests.push(route.request().postDataJSON());
    approved = true;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        created_topic: true,
        slug: 'ww1',
        type: 'History',
        name: 'World War I',
        topic: 'History - World War I',
        facts_created: 42,
        total_facts: 42,
      }),
    });
  });

  await page.route(`${API_BASE}/api/admin/custom-topics/toggle-active`, async (route) => {
    const data = route.request().postDataJSON();
    isTopicActive = data.is_active;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ slug: data.slug, is_active: data.is_active }),
    });
  });

  await page.goto('/admin');
  await expect(page.getByRole('heading', { name: 'Admin Dashboard' })).toBeVisible();

  await page.getByRole('button', { name: 'topics' }).click();
  await expect(page.getByText('Custom Topic Catalogue')).toBeVisible();
  const candidateRow = page.getByRole('row').filter({ hasText: 'ww1' });
  await expect(candidateRow.getByText('World War I', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Approve' }).click();
  await expect(page.getByText('History - World War I approved successfully. 42 new facts created, 42 total facts available.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Deactivate' })).toBeVisible();
  expect(requests).toEqual([
    {
      type: 'History',
      name: 'World War I',
      slug: 'ww1',
      description: 'Admin-approved custom room for World War I.',
      source_topic: 'history',
      max_facts: 100,
    },
  ]);
});
