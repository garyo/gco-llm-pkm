import { STORAGE_KEYS } from './types';
import { fetchWithTimeout } from './utils';

/** Whether to start logged in. Always asks the server: with auth disabled it
 * reports valid even without a token. Only an explicit rejection means no --
 * after network or server errors (retried) a stored token is trusted, and a
 * stale one still ends at the login screen on the first 401. */
export async function checkAuth(): Promise<boolean> {
  const token = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);

  for (let attempt = 1; ; attempt++) {
    try {
      const res = await fetchWithTimeout('/verify-token', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      }, 5000);
      if (res.ok) return (await res.json()).valid === true;
      if (res.status < 500 && res.status !== 429) return false;
    } catch {
      // Offline, timed out, or a proxy error page; retry below.
    }
    if (attempt === 3) return token !== null;
    await new Promise((r) => setTimeout(r, attempt * 1000));
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
