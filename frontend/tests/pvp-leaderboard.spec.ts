import { expect, test } from '@playwright/test';
import {
  apiPattern,
  mockApiJson,
  normalUser,
} from './helpers';

test('PvP room loads rating and renders leaderboard entries', async ({ page }) => {
  // Mock authentication
  await mockApiJson(page, '/api/auth/me', { user: normalUser });
  await mockApiJson(page, '/api/auth/logout', { success: true });

  // Mock PvP user rating
  await mockApiJson(
    page,
    `/api/pvp/user/${normalUser.id}/rating`,
    {
      user_id: normalUser.id,
      elo_rating: 1250.0,
      total_matches: 10,
      total_wins: 6,
      total_losses: 4,
      total_draws: 0,
      win_streak: 2,
      best_streak: 3,
      win_rate: 60.0,
    }
  );

  // Mock PvP leaderboard
  await mockApiJson(
    page,
    '/api/pvp/leaderboard\\?limit=10',
    {
      entries: [
        {
          rank: 1,
          user_id: '22222222-2222-4222-8222-222222222222',
          username: 'pvp_master',
          elo_rating: 1500.0,
          total_wins: 20,
          total_matches: 25,
          win_rate: 80.0,
        },
        {
          rank: 2,
          user_id: normalUser.id,
          username: normalUser.username,
          elo_rating: 1250.0,
          total_wins: 6,
          total_matches: 10,
          win_rate: 60.0,
        },
      ],
      total_players: 2,
    }
  );

  // Go to PvP Room at correct route
  await page.goto('/rooms/pvp');

  // Verify headers and elements are loaded
  await expect(page.getByRole('heading', { name: 'PvP Arena' })).toBeVisible();
  await expect(page.getByText('Your Elo Rating')).toBeVisible();
  await expect(page.getByText('1250', { exact: true })).toBeVisible(); // Elo rating value

  // Verify leaderboard is displayed
  await expect(page.getByRole('heading', { name: 'Leaderboard' })).toBeVisible();
  await expect(page.getByText('pvp_master')).toBeVisible();
  await expect(page.getByText('1500 Elo')).toBeVisible();
  await expect(page.getByText('scholar_user')).toBeVisible();
  await expect(page.getByText('1250 Elo')).toBeVisible();
});
