/** Playwright smoke coverage for security smoke.spec behavior. */

import { expect, test } from '@playwright/test';

const API_BASE = process.env.VITE_API_URL ?? 'http://localhost:8000';

test('signup uses cookie auth without legacy private localStorage keys', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') {
      errors.push(message.text());
    }
  });

  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const email = `pw-smoke-${suffix}@example.com`;
  const username = `pw_smoke_${suffix}`;
  const password = `SmokePass-${suffix}!A1`;

  await page.goto('/signup');
  await page.getByPlaceholder('Name').fill(username);
  await page.getByPlaceholder('Email').fill(email);
  await page.getByPlaceholder('Password').fill(password);
  await page.getByRole('button', { name: 'Sign Up', exact: true }).click();

  await expect(page).toHaveURL(/\/dashboard/);

  const localStorageSnapshot = await page.evaluate(() => ({
    adaptiq_token: window.localStorage.getItem('adaptiq_token'),
    adaptiq_user: window.localStorage.getItem('adaptiq_user'),
    adaptiq_scholar_history: window.localStorage.getItem('adaptiq_scholar_history'),
  }));
  expect(localStorageSnapshot).toEqual({
    adaptiq_token: null,
    adaptiq_user: null,
    adaptiq_scholar_history: null,
  });

  const me = await page.evaluate(async (apiBase) => {
    const response = await fetch(`${apiBase}/api/auth/me`, { credentials: 'include' });
    return { ok: response.ok, status: response.status };
  }, API_BASE);
  expect(me.ok, `GET /api/auth/me returned ${me.status}`).toBeTruthy();

  const topics = await page.evaluate(async (apiBase) => {
    const response = await fetch(`${apiBase}/api/custom/topics`, { credentials: 'include' });
    return {
      ok: response.ok,
      status: response.status,
      payload: await response.json().catch(() => null),
    };
  }, API_BASE);
  expect(topics.ok, `GET /api/custom/topics returned ${topics.status}`).toBeTruthy();
  const topicPayload = topics.payload;
  expect(
    Array.isArray(topicPayload.topics)
      && topicPayload.topics.some((topic: { slug?: string; total_facts?: number }) => (
        topic.slug === 'ww1' && Number(topic.total_facts ?? 0) > 0
      )),
  ).toBeTruthy();

  expect(errors).toEqual([]);
});
