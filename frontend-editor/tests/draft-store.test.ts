import { describe, expect, test } from 'bun:test';
import { DraftStore, MAX_DRAFTS, MAX_DRAFT_CHARS } from '@pkm/editor/draft-store';

class MemoryStorage implements Storage {
  map = new Map<string, string>();
  quota = Infinity;
  get length() {
    return this.map.size;
  }
  clear() {
    this.map.clear();
  }
  getItem(k: string) {
    return this.map.get(k) ?? null;
  }
  key(i: number) {
    return [...this.map.keys()][i] ?? null;
  }
  removeItem(k: string) {
    this.map.delete(k);
  }
  setItem(k: string, v: string) {
    const used = [...this.map.entries()].filter(([key]) => key !== k).reduce((n, [, val]) => n + val.length, 0);
    if (used + v.length > this.quota) throw new Error('QuotaExceededError');
    this.map.set(k, v);
  }
}

const rec = (path: string, savedAt: number, content = 'c') => ({ path, baseHash: 'h', content, savedAt });

describe('DraftStore', () => {
  test('round-trips a draft', () => {
    const store = new DraftStore(new MemoryStorage());
    expect(store.put(rec('a', 1))).toBe('stored');
    expect(store.get('a')?.savedAt).toBe(1);
    store.remove('a');
    expect(store.get('a')).toBeNull();
  });

  test('keeps at most MAX_DRAFTS, evicting the oldest', () => {
    const store = new DraftStore(new MemoryStorage());
    for (let i = 0; i < MAX_DRAFTS + 2; i++) store.put(rec(`f${i}`, i));
    expect(store.list().length).toBe(MAX_DRAFTS);
    expect(store.get('f0')).toBeNull();
    expect(store.get('f1')).toBeNull();
    expect(store.get(`f${MAX_DRAFTS + 1}`)).not.toBeNull();
  });

  test('refuses oversize drafts', () => {
    const store = new DraftStore(new MemoryStorage());
    expect(store.put(rec('big', 1, 'x'.repeat(MAX_DRAFT_CHARS + 1)))).toBe('too-large');
  });

  test('on quota pressure it evicts other drafts and retries', () => {
    const storage = new MemoryStorage();
    const store = new DraftStore(storage);
    store.put(rec('old', 1, 'x'.repeat(100)));
    storage.quota = 250;
    expect(store.put(rec('new', 2, 'y'.repeat(150)))).toBe('stored');
    expect(store.get('old')).toBeNull();
    expect(store.get('new')).not.toBeNull();
  });

  test('a throwing storage degrades to empty', () => {
    const broken = new Proxy({} as Storage, { get: () => () => { throw new Error('private mode'); } });
    const store = new DraftStore(broken);
    expect(store.get('a')).toBeNull();
    expect(store.put(rec('a', 1))).toBe('failed');
    expect(store.list()).toEqual([]);
  });
});
