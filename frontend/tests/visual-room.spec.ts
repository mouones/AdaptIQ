import { expect, test } from '@playwright/test';
import {
  apiPattern,
  fulfillJson,
  mockAuthenticatedUser,
  normalUser,
} from './helpers';

const visualQuestion = {
  id: '22222222-2222-4222-8222-222222222222',
  image_url: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="240" height="160"%3E%3Crect width="240" height="160" fill="%23f5f2e7"/%3E%3Ccircle cx="120" cy="80" r="48" fill="%23d4af37"/%3E%3C/svg%3E',
  text: 'Which region is highlighted in this visual source?',
  options: ['Nile Delta', 'Andes', 'Sahara', 'Balkan Peninsula'],
  topic: 'Geography',
  level: 2,
  question_type: 'M',
  options_count: 4,
  shape_path: null,
  shape_view_box: null,
  show_flag: false,
  show_shape: false,
};

test('visual room starts a session, shows a hint, and records an answer', async ({ page }) => {
  const startRequests: unknown[] = [];
  const submitRequests: unknown[] = [];

  await mockAuthenticatedUser(page, normalUser);

  await page.route(apiPattern('/api/visual/start-session'), async (route) => {
    startRequests.push(route.request().postDataJSON());
    await fulfillJson(route, {
      session_id: 'visual-session-1',
      topic: 'Geography',
      level: 2,
      total_questions: 10,
    });
  });

  await page.route(apiPattern('/api/visual/next\\?.*'), async (route) => {
    await fulfillJson(route, visualQuestion);
  });

  await page.route(apiPattern('/api/visual/hint\\?.*'), async (route) => {
    await fulfillJson(route, {
      hint: 'Look for the river-shaped coastline clue.',
    });
  });

  await page.route(apiPattern('/api/visual/submit'), async (route) => {
    submitRequests.push(route.request().postDataJSON());
    await fulfillJson(route, {
      is_correct: true,
      correct_answer: 'Nile Delta',
      explanation: 'The highlighted fan shape matches a river delta.',
      next_question: null,
    });
  });

  await page.goto('/rooms/visual');
  await expect(page.getByRole('heading', { name: 'Read the Image' })).toBeVisible();

  await page.getByRole('button', { name: /Geography.*Reason about places/i }).click();
  await page.getByRole('button', { name: '2' }).click();
  await page.getByRole('button', { name: 'Begin Session' }).click();

  await expect(page.getByText('Which region is highlighted in this visual source?')).toBeVisible();
  await page.getByRole('button', { name: 'Hint' }).click();
  await expect(page.getByText('Look for the river-shaped coastline clue.')).toBeVisible();

  await page.getByRole('button', { name: 'Nile Delta' }).click();
  await expect(page.getByText('Correct answer: Nile Delta')).toBeVisible();
  await expect(page.getByText('The highlighted fan shape matches a river delta.')).toBeVisible();

  expect(startRequests).toEqual([
    {
      user_id: normalUser.id,
      topic: 'Geography',
      level: 2,
    },
  ]);
  expect(submitRequests).toEqual([
    {
      session_id: 'visual-session-1',
      user_id: normalUser.id,
      question_id: visualQuestion.id,
      chosen_answer: 'Nile Delta',
      user_time_ms: expect.any(Number),
    },
  ]);
});
