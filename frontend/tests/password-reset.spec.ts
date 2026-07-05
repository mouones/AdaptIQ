import { expect, test } from '@playwright/test';
import { apiPattern, fulfillJson } from './helpers';

test('forgot and reset password forms complete with mocked auth API', async ({ page }) => {
  const requestedEmails: string[] = [];
  const resetPayloads: unknown[] = [];

  await page.route(apiPattern('/api/auth/forgot-password'), async (route) => {
    requestedEmails.push(route.request().postDataJSON().email);
    await fulfillJson(route, { success: true });
  });

  await page.route(apiPattern('/api/auth/reset-password'), async (route) => {
    resetPayloads.push(route.request().postDataJSON());
    await fulfillJson(route, { success: true });
  });

  await page.goto('/forgot-password');
  await expect(page.getByRole('heading', { name: 'Forgot Password' })).toBeVisible();

  await page.getByPlaceholder('Email').fill('Scholar@Example.COM');
  await page.getByRole('button', { name: 'Send Reset Code' }).click();

  await expect(page.getByText(/reset code has been sent/i)).toBeVisible();
  await page.getByRole('button', { name: 'Enter Reset Code' }).click();

  await expect(page).toHaveURL(/\/reset-password$/);
  await expect(page.getByPlaceholder('Email')).toHaveValue('Scholar@Example.COM');

  await page.getByPlaceholder('6-digit reset code').fill('123456');
  await page.getByPlaceholder(/New password/).fill('StrongPass1!');
  await page.getByRole('button', { name: 'Reset Password' }).click();

  await expect(page.getByText('Your password has been reset successfully.')).toBeVisible();
  await page.getByRole('button', { name: 'Go to Login' }).click();
  await expect(page).toHaveURL(/\/login$/);

  expect(requestedEmails).toEqual(['scholar@example.com']);
  expect(resetPayloads).toEqual([
    {
      email: 'scholar@example.com',
      code: '123456',
      new_password: 'StrongPass1!',
    },
  ]);
});
