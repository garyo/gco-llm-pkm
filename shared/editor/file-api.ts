// HTTP client for the note-file endpoints, shared by both editor consumers.

import { STORAGE_KEYS } from './types';

export interface FileData {
  content: string;
  path: string;
  hash: string;
  modified: number;
  size: number;
}

export interface SavedResult {
  status: 'saved' | 'merged' | 'unchanged' | 'exists';
  path: string;
  hash: string;
  modified: number;
  size: number;
  /** Present only when the server merged concurrent edits into what it wrote. */
  content?: string;
}

export type ConflictReason = 'stale' | 'overlap' | 'base_unknown';

export interface ConflictResult {
  status: 'conflict';
  reason: ConflictReason;
  message?: string;
  theirs?: FileData;
  /** Conflict-marked three-way merge, when the server could produce one. */
  merged?: string;
}

export type SaveResult = SavedResult | ConflictResult;

export interface SaveOptions {
  /** Hash of the content the edits were made against; omit to overwrite unconditionally. */
  baseHash?: string | null;
  /** Legacy optimistic-concurrency token, still honoured by older servers. */
  baseMtime?: number | null;
  /** Let the request outlive the page (pagehide flush). Bodies must stay under ~64 KB. */
  keepalive?: boolean;
  createOnly?: boolean;
  timeoutMs?: number;
}

export class FileApi {
  constructor(private readonly onUnauthorized: () => void = () => {}) {}

  async load(path: string, timeoutMs = 15000): Promise<FileData> {
    const res = await this.request(fileUrl(path), { method: 'GET' }, timeoutMs);
    if (!res.ok) throw new Error(`Failed to load file: ${res.status}`);
    return res.json();
  }

  async save(path: string, content: string, opts: SaveOptions = {}): Promise<SaveResult> {
    const body: Record<string, unknown> = { content };
    if (opts.baseHash) body.base_hash = opts.baseHash;
    if (opts.baseMtime != null) body.expected_mtime = opts.baseMtime;

    const url = fileUrl(path) + (opts.createOnly ? '?create_only=true' : '');
    const res = await this.request(
      url,
      { method: 'PUT', body: JSON.stringify(body), keepalive: opts.keepalive },
      opts.timeoutMs ?? 30000,
    );
    if (res.status === 409) return conflictFrom(await res.json());
    if (!res.ok) throw new Error(`Failed to save file: ${res.status}`);
    return res.json();
  }

  /** Ask the server to merge `content` (edited against `baseHash`) with what is on disk, without writing. */
  async merge(path: string, content: string, baseHash: string | null): Promise<SaveResult> {
    const res = await this.request(
      `${fileUrl(path)}/merge`,
      { method: 'POST', body: JSON.stringify({ content, base_hash: baseHash }) },
      15000,
    );
    if (res.status === 409) return conflictFrom(await res.json());
    if (!res.ok) throw new Error(`Failed to merge file: ${res.status}`);
    return res.json();
  }

  private async request(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, { ...init, headers: authHeaders(), signal: controller.signal });
      if (res.status === 401) {
        this.onUnauthorized();
        throw new Error('Unauthorized');
      }
      return res;
    } finally {
      clearTimeout(timer);
    }
  }
}

function fileUrl(path: string): string {
  return `/api/file/${encodeURIComponent(path)}`;
}

function conflictFrom(body: Record<string, unknown>): ConflictResult {
  return {
    status: 'conflict',
    reason: (body.reason as ConflictReason) ?? 'stale',
    message: body.message as string | undefined,
    theirs: body.theirs as FileData | undefined,
    merged: body.merged as string | undefined,
  };
}

export function authHeaders(): HeadersInit {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

export function isAbortError(e: unknown): boolean {
  return e instanceof Error && e.name === 'AbortError';
}
