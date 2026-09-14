// Unsaved editor text, persisted per file so a killed tab never loses typing.
//
// localStorage rather than IndexedDB on purpose: the write that matters happens
// inside `pagehide`, and only a synchronous store is guaranteed to land before a
// mobile browser discards the page.

export interface DraftRecord {
  path: string;
  /** Hash of the on-disk content the draft was edited against (null if unknown). */
  baseHash: string | null;
  content: string;
  savedAt: number;
}

export type DraftPutResult = 'stored' | 'too-large' | 'failed';

export const MAX_DRAFTS = 5;
export const MAX_DRAFT_CHARS = 1_500_000;

export class DraftStore {
  constructor(
    private readonly storage: Storage,
    private readonly prefix = 'pkm-draft:',
  ) {}

  get(path: string): DraftRecord | null {
    try {
      const raw = this.storage.getItem(this.prefix + path);
      return raw ? (JSON.parse(raw) as DraftRecord) : null;
    } catch {
      return null;
    }
  }

  put(record: DraftRecord): DraftPutResult {
    if (record.content.length > MAX_DRAFT_CHARS) return 'too-large';
    const key = this.prefix + record.path;
    const value = JSON.stringify(record);
    try {
      this.evict(MAX_DRAFTS - 1, record.path);
      this.storage.setItem(key, value);
      return 'stored';
    } catch {
      // Quota: make room by dropping every other draft, then try once more.
      try {
        this.evict(0, record.path);
        this.storage.setItem(key, value);
        return 'stored';
      } catch {
        return 'failed';
      }
    }
  }

  remove(path: string): void {
    try {
      this.storage.removeItem(this.prefix + path);
    } catch {
      // Nothing to do; a missing draft is the desired end state.
    }
  }

  list(): DraftRecord[] {
    const records: DraftRecord[] = [];
    try {
      for (let i = 0; i < this.storage.length; i++) {
        const key = this.storage.key(i);
        if (!key?.startsWith(this.prefix)) continue;
        const record = this.get(key.slice(this.prefix.length));
        if (record) records.push(record);
      }
    } catch {
      // Unreadable storage behaves like an empty one.
    }
    return records.sort((a, b) => a.savedAt - b.savedAt);
  }

  /** Drop the oldest drafts (other than `keepPath`) until at most `keep` remain. */
  private evict(keep: number, keepPath: string): void {
    const others = this.list().filter((r) => r.path !== keepPath);
    for (const record of others.slice(0, Math.max(0, others.length - keep))) {
      this.remove(record.path);
    }
  }
}
