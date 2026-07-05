import { expect, test } from '@playwright/test';
import {
  apiPattern,
  fulfillJson,
  mockApiJson,
  mockAuthenticatedUser,
  normalUser,
  type TestUser,
} from './helpers';

test('profile page loads user stats and saves profile edits', async ({ page }) => {
  let user: TestUser = { ...normalUser };
  const patchPayloads: unknown[] = [];

  await page.route(apiPattern('/api/auth/me'), async (route) => {
    await fulfillJson(route, { user });
  });
  await mockApiJson(page, '/api/auth/logout', { success: true });
  await mockApiJson(page, '/api/auth/stats', {
    id: user.id,
    points: 1500,
    level: 'Expert',
    total_questions: 80,
    global_accuracy: 81,
    daily_questions: 8,
    daily_accuracy: 88,
    learning_time_minutes: 22,
    daily_points: 55,
    streak_days: 6,
  });
  await mockApiJson(page, `/api/custom/user/${user.id}/concept-mastery`, { concepts: [] });

  await page.route(apiPattern('/api/auth/profile'), async (route) => {
    const payload = route.request().postDataJSON();
    patchPayloads.push(payload);
    user = {
      ...user,
      username: payload.username ?? user.username,
      email: payload.email ?? user.email,
    };
    await fulfillJson(route, user);
  });

  await page.goto('/profile');

  await expect(page.getByRole('heading', { name: 'Profile', exact: true })).toBeVisible();
  await expect(page.getByText('scholar_user')).toBeVisible();
  await expect(page.getByText('1,250', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Edit Profile Parameters' }).click();
  await page.getByPlaceholder('Username').fill('updated_scholar');
  await page.getByRole('button', { name: 'Save Changes' }).click();

  await expect(page.getByText('Profile updated successfully.')).toBeVisible();
  await expect(page.getByPlaceholder('Username')).toHaveValue('updated_scholar');
  expect(patchPayloads).toEqual([{ username: 'updated_scholar' }]);
});

test('profile requests and confirms verified email change', async ({ page }) => {
  let user: TestUser = { ...normalUser };
  const requestPayloads: unknown[] = [];
  const confirmPayloads: unknown[] = [];

  await page.route(apiPattern('/api/auth/me'), async (route) => {
    await fulfillJson(route, { user });
  });
  await mockApiJson(page, '/api/auth/logout', { success: true });
  await mockApiJson(page, '/api/auth/stats', {
    id: user.id,
    points: 1500,
    level: 'Expert',
    total_questions: 80,
    global_accuracy: 81,
    daily_questions: 8,
    daily_accuracy: 88,
    learning_time_minutes: 22,
    daily_points: 55,
    streak_days: 6,
  });
  await mockApiJson(page, `/api/custom/user/${user.id}/concept-mastery`, { concepts: [] });

  await page.route('**/api/auth/profile/email-change/request*', async (route) => {
    requestPayloads.push(JSON.parse(route.request().postData() || '{}'));
    await fulfillJson(route, { message: 'Verification code sent to the new email address' });
  });

  await page.route('**/api/auth/profile/email-change/confirm*', async (route) => {
    const payload = JSON.parse(route.request().postData() || '{}');
    confirmPayloads.push(payload);
    user = {
      ...user,
      email: payload.new_email,
    };
    await fulfillJson(route, user);
  });

  await page.goto('/profile');
  await page.getByRole('button', { name: 'Edit Profile Parameters' }).click();
  await page.getByPlaceholder('Email').fill('new.scholar@adaptiq.dev');
  await page.getByRole('button', { name: 'Save Changes' }).click();
  await expect(page.getByText('Verification code sent to new.scholar@adaptiq.dev.')).toBeVisible();

  await page.getByPlaceholder('Verification code').fill('123456');
  await page.getByRole('button', { name: 'Confirm Email' }).click();

  await expect(page.getByText('Email updated successfully.')).toBeVisible();
  await expect(page.getByPlaceholder('Email')).toHaveValue('new.scholar@adaptiq.dev');
  expect(requestPayloads).toEqual([{ new_email: 'new.scholar@adaptiq.dev' }]);
  expect(confirmPayloads).toEqual([{ new_email: 'new.scholar@adaptiq.dev', code: '123456' }]);
});

test('profile shows validation message when password fields are incomplete', async ({ page }) => {
  await mockAuthenticatedUser(page, normalUser);
  await mockApiJson(page, '/api/auth/stats', {
    id: normalUser.id,
    points: normalUser.points,
    level: normalUser.level,
    total_questions: 10,
    global_accuracy: 70,
    daily_questions: 1,
    daily_accuracy: 100,
    learning_time_minutes: 5,
    daily_points: 10,
    streak_days: 1,
  });
  await mockApiJson(page, `/api/custom/user/${normalUser.id}/concept-mastery`, { concepts: [] });

  await page.goto('/profile');
  await page.getByRole('button', { name: 'Edit Profile Parameters' }).click();
  await page.getByPlaceholder('Minimum 8 characters').fill('StrongPass1!');
  await page.getByRole('button', { name: 'Save Changes' }).click();

  await expect(page.getByText('To change password, provide both current and new password.')).toBeVisible();
});
