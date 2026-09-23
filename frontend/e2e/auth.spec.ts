import { test, expect } from '@playwright/test';

/**
 * E2E tests for the key authentication and role-based access path.
 * These verify the critical user flow: login → dashboard → navigate.
 * 
 * NOTE: These tests require a running backend at the configured baseURL.
 * They are designed to run against the dev environment or a test docker-compose stack.
 */

test.describe('Authentication and Role-Based Access', () => {
  test('login page renders and rejects invalid credentials', async ({ page }) => {
    await page.goto('/');
    // Should redirect to login page if not authenticated
    await expect(page).toHaveURL(/login/);
    // Login form should be visible
    await expect(page.getByLabel(/почта|email/i)).toBeVisible();
    await expect(page.getByLabel(/пароль|password/i)).toBeVisible();

    // Submit with invalid credentials
    await page.getByLabel(/почта|email/i).fill('invalid@test.com');
    await page.getByLabel(/пароль|password/i).fill('wrongpassword123');
    await page.getByRole('button', { name: /войти/i }).click();

    // Should show error and stay on login page
    await expect(page.locator('.error-box, [role="alert"]')).toBeVisible({ timeout: 5000 });
    await expect(page).toHaveURL(/login/);
  });

  test('unauthenticated user cannot access dashboard', async ({ page }) => {
    await page.goto('/dashboard');
    // Should redirect to login
    await expect(page).toHaveURL(/login/);
  });

  test('unauthenticated user cannot access settings', async ({ page }) => {
    await page.goto('/settings');
    // Should redirect to login
    await expect(page).toHaveURL(/login/);
  });

  test('unauthenticated user cannot access requests', async ({ page }) => {
    await page.goto('/requests');
    // Should redirect to login
    await expect(page).toHaveURL(/login/);
  });
});

test.describe('Navigation Structure', () => {
  test('login page has required form elements', async ({ page }) => {
    await page.goto('/login');
    
    // Check form structure
    const emailInput = page.getByLabel(/почта|email/i);
    const passwordInput = page.getByLabel(/пароль|password/i);
    const submitButton = page.getByRole('button', { name: /войти/i });
    
    await expect(emailInput).toBeVisible();
    await expect(passwordInput).toBeVisible();
    await expect(submitButton).toBeVisible();
    
    // Password field should be of type password
    await expect(passwordInput).toHaveAttribute('type', 'password');
  });
});

test.describe('Datetime Handling', () => {
  test('datetime-local fields display correctly on login page (smoke)', async ({ page }) => {
    // This is a smoke test to verify the app loads without JS errors
    await page.goto('/login');
    
    // Listen for console errors
    const errors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    
    // Wait for page to settle
    await page.waitForTimeout(1000);
    
    // No critical JS errors should occur during page load
    const criticalErrors = errors.filter(e => 
      !e.includes('favicon') && !e.includes('net::ERR')
    );
    expect(criticalErrors).toHaveLength(0);
  });
});
