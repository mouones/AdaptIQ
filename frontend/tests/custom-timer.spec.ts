import { expect, test } from '@playwright/test';
import {
  apiPattern,
  fulfillJson,
  mockAuthenticatedUser,
  mockDashboardApis,
  normalUser,
} from './helpers';

const sampleQuestion = {
  id: '33333333-3333-4333-8333-333333333333',
  text: 'Which treaty ended World War I?',
  options: ['Treaty of Versailles', 'Treaty of Paris', 'Treaty of Ghent', 'Treaty of Utrecht'],
  explanation: '',
  fact_id: null,
  concept_id: null,
};

test('custom room timer pauses while the next question is loading', async ({ page }) => {
  let generateCount = 0;

  await mockAuthenticatedUser(page, normalUser);
  await mockDashboardApis(page, normalUser);

  await page.route(apiPattern('/api/custom/topics'), async (route) => {
    await fulfillJson(route, {
      topics: [{
        slug: 'history-ww1',
        type: 'History',
        name: 'World War I',
        description: 'The Great War era.',
        total_facts: 120,
      }],
    });
  });

  await page.route(apiPattern('/api/custom/start-session'), async (route) => {
    await fulfillJson(route, {
      session_id: 'custom-session-1',
      progress_percentage: 12.5,
      concept_id: null,
    });
  });

  await page.route(apiPattern('/api/custom/generate-question'), async (route) => {
    generateCount += 1;
    if (generateCount > 1) {
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
    await fulfillJson(route, sampleQuestion);
  });

  await page.route(apiPattern('/api/custom/submit-answer'), async (route) => {
    await fulfillJson(route, {
      is_correct: true,
      correct_answer: 'Treaty of Versailles',
      explanation: 'Signed in 1919.',
      new_progress_percentage: 15.0,
    });
  });

  await page.goto('/rooms/custom');
  await page.getByRole('button', { name: /History/i }).click();
  await page.getByRole('button', { name: /World War I/i }).click();

  await expect(page.getByText('Which treaty ended World War I?')).toBeVisible();
  await expect(page.getByText(/\d+s/)).toBeVisible();

  await page.getByRole('button', { name: 'Treaty of Versailles' }).click();
  await expect(page.getByText('Correct!')).toBeVisible();
  await expect(page.getByText('—')).toBeVisible();

  await page.getByRole('button', { name: /Continue Research/i }).click();
  await expect(page.getByText('Preparing your next inquiry…')).toBeVisible();
  await expect(page.getByText('…')).toBeVisible();

  const timerDuringLoad = page.getByText(/^\d+s$/);
  await expect(timerDuringLoad).toHaveCount(0);

  await expect(page.getByText('Which treaty ended World War I?')).toBeVisible({ timeout: 10000 });
  await expect(page.getByText(/\d+s/)).toBeVisible();
});
