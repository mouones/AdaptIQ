import { expect, test } from '@playwright/test';
import {
  mockAuthenticatedUser,
  mockDashboardApis,
  mockUnauthenticated,
  normalUser,
} from './helpers';

test('protected routes redirect unauthenticated users to login', async ({ page }) => {
  await mockUnauthenticated(page);

  await page.goto('/dashboard');

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole('heading', { name: 'Log In' })).toBeVisible();
});

test('non-admin users are redirected away from admin dashboard', async ({ page }) => {
  await mockAuthenticatedUser(page, normalUser);
  await mockDashboardApis(page, normalUser);

  await page.goto('/admin');

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole('heading', { name: 'Welcome, Scholar' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Admin' })).toHaveCount(0);
});

test('dashboard room cards and sidebar navigate across room pages', async ({ page }) => {
  await mockAuthenticatedUser(page, normalUser);
  await mockDashboardApis(page, normalUser);

  await page.goto('/dashboard');
  await expect(page.getByRole('heading', { name: 'Welcome, Scholar' })).toBeVisible();

  await page.locator('#room-classic').getByRole('button', { name: /Enter Room/i }).click();
  await expect(page).toHaveURL(/\/rooms\/classic$/);
  await expect(page.getByRole('heading', { name: 'Choose Your Path' })).toBeVisible();

  await page.locator('#sidebar-room-visual').click();
  await expect(page).toHaveURL(/\/rooms\/visual$/);
  await expect(page.getByRole('heading', { name: 'Read the Image' })).toBeVisible();
});
