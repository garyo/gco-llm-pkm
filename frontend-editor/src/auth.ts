import { STORAGE_KEYS } from './types';
import { fetchWithTimeout } from './utils';

/** Check if the stored JWT token is valid. Returns true if authenticated. */
export async function checkAuth(): Promise<boolean> {
  const token = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
  if (!token) return false;

  try {
    const res = await fetchWithTimeout('/verify-token', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
    }, 5000);

    return res.ok;
  } catch {
    return false;
  }
}

/** Attempt login. Returns true on success. */
export async function handleLogin(password: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetchWithTimeout('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    }, 10000);

    if (!res.ok) {
      const data = await res.json().catch(() => ({ error: 'Login failed' }));
      return { ok: false, error: data.error || `Login failed (${res.status})` };
    }

    const data = await res.json();
    if (data.token) {
      localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, data.token);
      return { ok: true };
    }
    return { ok: false, error: 'No token in response' };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : 'Network error';
    return { ok: false, error: msg };
  }
}

/** Show the login screen, hide editor and admin. */
export function showLogin(): void {
  document.getElementById('login-container')!.classList.remove('hidden');
  document.getElementById('editor-app')!.classList.add('hidden');
  document.getElementById('admin-app')!.classList.add('hidden');
}

/** Hide login, show editor. */
export function showEditor(): void {
  document.getElementById('login-container')!.classList.add('hidden');
  document.getElementById('editor-app')!.classList.remove('hidden');
  document.getElementById('admin-app')!.classList.add('hidden');
}

/** Hide login and editor, show admin. */
export function showAdmin(): void {
  document.getElementById('login-container')!.classList.add('hidden');
  document.getElementById('editor-app')!.classList.add('hidden');
  document.getElementById('admin-app')!.classList.remove('hidden');
}

/** Handle 401 responses: clear token and show login. */
export function handle401(): void {
  localStorage.removeItem(STORAGE_KEYS.AUTH_TOKEN);
  showLogin();
}
