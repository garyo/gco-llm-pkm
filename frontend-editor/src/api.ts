import type { FileInfo } from './types';
import { getAuthHeaders, fetchWithTimeout } from './utils';
import { handle401 } from './auth';

/** Load the file list from the backend. */
export async function loadFileList(): Promise<FileInfo[]> {
  const res = await fetchWithTimeout('/api/files', {
    headers: getAuthHeaders(),
  }, 15000);

  if (res.status === 401) {
    handle401();
    throw new Error('Unauthorized');
  }

  if (!res.ok) throw new Error(`Failed to load files: ${res.status}`);
  return res.json();
}

/** Resolve an org-id UUID to a file path. */
export async function resolveOrgId(
  uuid: string,
): Promise<{ path: string; line?: number } | null> {
  try {
    const res = await fetchWithTimeout(
      `/api/resolve-org-id/${encodeURIComponent(uuid)}`,
      { headers: getAuthHeaders() },
      5000,
    );
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}
