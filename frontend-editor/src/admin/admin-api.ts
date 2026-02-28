import type { OAuthStatus, LearnedRule, SystemPrompt } from '../types';
import { getAuthHeaders, fetchWithTimeout } from '../utils';
import { handle401 } from '../auth';

async function authFetch(url: string, options: RequestInit = {}, timeout = 15000): Promise<Response> {
  const res = await fetchWithTimeout(url, { ...options, headers: { ...getAuthHeaders(), ...options.headers } }, timeout);
  if (res.status === 401) {
    handle401();
    throw new Error('Unauthorized');
  }
  return res;
}

// --- OAuth ---

export async function getOAuthStatus(service: string): Promise<OAuthStatus> {
  const res = await authFetch(`/auth/${service}/status`);
  if (!res.ok) throw new Error(`Failed to get ${service} status: ${res.status}`);
  return res.json();
}

export async function disconnectOAuth(service: string): Promise<void> {
  const res = await authFetch(`/auth/${service}/disconnect`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed to disconnect ${service}: ${res.status}`);
}

// --- System Prompts ---

export async function getSystemPrompt(type: 'web' | 'mcp'): Promise<SystemPrompt> {
  const res = await authFetch(`/api/system-prompt/${type}`);
  if (!res.ok) throw new Error(`Failed to load ${type} prompt: ${res.status}`);
  return res.json();
}

export async function saveSystemPrompt(
  type: 'web' | 'mcp',
  content: string,
  expectedMtime?: number,
): Promise<{ status: string; modified: number }> {
  const body: Record<string, unknown> = { content };
  if (expectedMtime != null) body.expected_mtime = expectedMtime;

  const res = await authFetch(`/api/system-prompt/${type}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (res.status === 409) {
    const data = await res.json();
    throw new Error(`Conflict: ${data.error}`);
  }
  if (!res.ok) throw new Error(`Failed to save ${type} prompt: ${res.status}`);
  return res.json();
}

// --- Learned Rules ---

export async function getLearnedRules(): Promise<LearnedRule[]> {
  const res = await authFetch('/api/learned-rules');
  if (!res.ok) throw new Error(`Failed to load rules: ${res.status}`);
  return res.json();
}

export async function updateLearnedRule(
  id: number,
  updates: Partial<Pick<LearnedRule, 'rule_text' | 'is_active' | 'confidence'>>,
): Promise<void> {
  const res = await authFetch(`/api/learned-rules/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Failed to update rule ${id}: ${res.status}`);
}

export async function deleteLearnedRule(id: number): Promise<void> {
  const res = await authFetch(`/api/learned-rules/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Failed to delete rule ${id}: ${res.status}`);
}

// --- Scheduled Tasks ---

export async function getScheduledTasks(): Promise<unknown[]> {
  const res = await authFetch('/api/scheduled-tasks');
  if (!res.ok) throw new Error(`Failed to load tasks: ${res.status}`);
  return res.json();
}

export async function createScheduledTask(params: Record<string, unknown>): Promise<unknown> {
  const res = await authFetch('/api/scheduled-tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Create failed: ${res.status}`);
  }
  return res.json();
}

export async function toggleScheduledTask(id: number): Promise<unknown> {
  const res = await authFetch(`/api/scheduled-tasks/${id}/toggle`, { method: 'POST' });
  if (!res.ok) throw new Error(`Toggle failed: ${res.status}`);
  return res.json();
}

export async function deleteScheduledTask(id: number): Promise<void> {
  const res = await authFetch(`/api/scheduled-tasks/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Delete failed: ${res.status}`);
  }
}

export async function runScheduledTaskNow(id: number): Promise<unknown> {
  const res = await authFetch(`/api/scheduled-tasks/${id}/run`, { method: 'POST' });
  if (!res.ok) throw new Error(`Run failed: ${res.status}`);
  return res.json();
}

export async function getScheduledTaskRuns(): Promise<unknown[]> {
  const res = await authFetch('/api/scheduled-tasks/runs');
  if (!res.ok) throw new Error(`Failed to load task runs: ${res.status}`);
  return res.json();
}

export async function getScheduledTaskBudget(): Promise<unknown> {
  const res = await authFetch('/api/scheduled-tasks/budget');
  if (!res.ok) throw new Error(`Failed to load budget: ${res.status}`);
  return res.json();
}

// --- Self-Improvement ---

export async function triggerSelfImprove(): Promise<{ status: string; message?: string }> {
  const res = await authFetch('/admin/self-improve', { method: 'POST' }, 60000);
  if (!res.ok) {
    const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    throw new Error(data.error || `Failed: ${res.status}`);
  }
  return res.json();
}

export async function getSelfImproveLog(): Promise<unknown> {
  const res = await authFetch('/admin/self-improve/log');
  if (!res.ok) throw new Error(`Failed to load log: ${res.status}`);
  return res.json();
}

export async function getSelfImproveMemory(): Promise<unknown> {
  const res = await authFetch('/admin/self-improve/memory');
  if (!res.ok) throw new Error(`Failed to load memory: ${res.status}`);
  return res.json();
}
