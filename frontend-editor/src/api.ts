import type { FileInfo, EditorState } from './types';
import { getAuthHeaders, fetchWithTimeout } from './utils';
import { handle401 } from './auth';

/** Load the file list from the backend. */
export async function loadFileList(_state: EditorState): Promise<FileInfo[]> {
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

/** Load a single file's content. */
export async function loadFile(
  filepath: string,
): Promise<{ content: string; path: string; size: number; modified: number }> {
  const res = await fetchWithTimeout(
    `/api/file/${encodeURIComponent(filepath)}`,
    { headers: getAuthHeaders() },
    15000,
  );

  if (res.status === 401) {
    handle401();
    throw new Error('Unauthorized');
  }

  if (!res.ok) throw new Error(`Failed to load file: ${res.status}`);
  return res.json();
}

/** Save a file. Returns response data or throws on error. */
export async function saveFile(
  filepath: string,
  content: string,
  expectedMtime: number | null,
  force: boolean = false,
): Promise<{ path: string; size: number; modified: number; status?: number }> {
  const body: Record<string, unknown> = { content };
  if (!force && expectedMtime !== null) {
    body.expected_mtime = expectedMtime;
  }

  const res = await fetchWithTimeout(
    `/api/file/${encodeURIComponent(filepath)}`,
    {
      method: 'PUT',
      headers: getAuthHeaders(),
      body: JSON.stringify(body),
    },
    30000,
  );

  if (res.status === 401) {
    handle401();
    throw new Error('Unauthorized');
  }

  if (res.status === 409) {
    return { path: filepath, size: 0, modified: 0, status: 409 };
  }

  if (!res.ok) throw new Error(`Failed to save file: ${res.status}`);
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

/** Create a file (PUT with create_only=true). */
export async function createFile(
  filepath: string,
  content: string,
): Promise<{ status: string; path: string; size: number; modified: number }> {
  const res = await fetchWithTimeout(
    `/api/file/${encodeURIComponent(filepath)}?create_only=true`,
    {
      method: 'PUT',
      headers: getAuthHeaders(),
      body: JSON.stringify({ content }),
    },
    15000,
  );

  if (res.status === 401) {
    handle401();
    throw new Error('Unauthorized');
  }

  if (!res.ok) throw new Error(`Failed to create file: ${res.status}`);
  return res.json();
}

/** Verify if a save actually completed despite timeout. */
export async function verifySaveCompleted(
  filepath: string,
  expectedContent: string,
): Promise<boolean> {
  if (!navigator.onLine) return false;

  try {
    const res = await fetchWithTimeout(
      `/api/file/${encodeURIComponent(filepath)}`,
      { headers: getAuthHeaders() },
      3000,
    );
    if (!res.ok) return false;
    const data = await res.json();
    return data.content === expectedContent;
  } catch {
    return false;
  }
}

/** Check if a backup file exists. */
export async function checkBackupExists(backupPath: string): Promise<boolean> {
  if (!navigator.onLine) return false;

  try {
    const res = await fetchWithTimeout(
      `/api/file/${encodeURIComponent(backupPath)}`,
      { headers: getAuthHeaders() },
      3000,
    );
    return res.ok;
  } catch {
    return false;
  }
}
