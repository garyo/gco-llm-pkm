import { describe, expect, test } from 'bun:test';
import { SaveController, MAX_WAIT_MS, type Clock } from '@pkm/editor/save-state';
import { DraftStore } from '@pkm/editor/draft-store';
import type { DocAdapter, Checkpoint } from '@pkm/editor/diff-apply';
import type { FileApi, FileData, SaveResult } from '@pkm/editor/file-api';

// ---------------------------------------------------------------------------
// Test doubles
// ---------------------------------------------------------------------------

class ManualClock implements Clock {
  private t = 1_000_000;
  private timers: { at: number; fn: () => void; id: number }[] = [];
  private nextId = 1;
  now() {
    return this.t;
  }
  setTimeout(fn: () => void, ms: number) {
    const id = this.nextId++;
    this.timers.push({ at: this.t + ms, fn, id });
    return id;
  }
  clearTimeout(id: unknown) {
    this.timers = this.timers.filter((t) => t.id !== id);
  }
  /** Advance time, firing due timers in order. */
  async tick(ms: number) {
    const target = this.t + ms;
    for (;;) {
      const due = this.timers.filter((t) => t.at <= target).sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      this.timers = this.timers.filter((t) => t.id !== due.id);
      this.t = due.at;
      due.fn();
      await flush();
    }
    this.t = target;
  }
}

/** Plain-string document; rebase applies the checkpoint->text diff over later edits by simple replacement of the unchanged tail. */
class StringDoc implements DocAdapter {
  text = '';
  composing = false;
  rebased: string[] = [];
  getText() {
    return this.text;
  }
  isComposing() {
    return this.composing;
  }
  checkpoint(): Checkpoint {
    return { text: this.text, since: null as never };
  }
  release() {}
  rebase(cp: Checkpoint, text: string) {
    // Edits since the checkpoint are, in these tests, always appended text.
    const typedSince = this.text.slice(cp.text.length);
    this.text = text + typedSince;
    this.rebased.push(text);
  }
}

class FakeApi {
  disk: FileData;
  calls: { content: string; baseHash?: string | null; baseMtime?: number | null; keepalive?: boolean }[] = [];
  nextResponse: ((content: string) => SaveResult | Error) | null = null;
  pending: (() => void)[] = [];
  hold = false;

  constructor(content: string) {
    this.disk = { content, path: 'org:a.org', hash: h(content), modified: 100, size: content.length };
  }

  async load(): Promise<FileData> {
    return { ...this.disk };
  }

  async save(_path: string, content: string, opts: { baseHash?: string | null; baseMtime?: number | null; keepalive?: boolean }) {
    this.calls.push({ content, ...opts });
    if (this.hold) await new Promise<void>((r) => this.pending.push(r));
    if (this.nextResponse) {
      const r = this.nextResponse(content);
      this.nextResponse = null;
      if (r instanceof Error) throw r;
      return r;
    }
    this.disk = { ...this.disk, content, hash: h(content), modified: this.disk.modified + 1, size: content.length };
    return { status: 'saved' as const, path: 'org:a.org', hash: this.disk.hash, modified: this.disk.modified, size: content.length };
  }

  async merge(): Promise<SaveResult> {
    throw new Error('not in these tests');
  }

  releaseHeld() {
    this.hold = false;
    for (const r of this.pending.splice(0)) r();
  }
}

class MemoryStorage implements Storage {
  private map = new Map<string, string>();
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
    this.map.set(k, v);
  }
}

function h(s: string) {
  return `h(${s})`;
}

const flush = () => new Promise<void>((r) => setTimeout(r, 0));

function setup(initial = 'hello\n', settings = { enabled: true, delayMs: 2000 }) {
  const clock = new ManualClock();
  const api = new FakeApi(initial);
  const doc = new StringDoc();
  const storage = new MemoryStorage();
  const drafts = new DraftStore(storage);
  const status: string[] = [];
  const conflicts: unknown[] = [];
  const dirtyChanges: boolean[] = [];
  const saver = new SaveController({
    api: api as unknown as FileApi,
    drafts,
    doc,
    settings: () => settings,
    onStatus: (m) => status.push(m),
    onDirtyChange: (d) => dirtyChanges.push(d),
    onConflict: (c) => conflicts.push(c),
    clock,
  });
  doc.text = initial;
  saver.onLoaded({ ...api.disk });
  const type = (s: string) => {
    doc.text += s;
    saver.noteDocChanged();
  };
  return { clock, api, doc, drafts, saver, status, conflicts, dirtyChanges, type, settings };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SaveController accounting', () => {
  test('keystrokes during an in-flight save stay dirty and get saved next', async () => {
    const { clock, api, saver, type } = setup();
    type('one ');
    api.hold = true;
    await clock.tick(2000); // autosave fires, request is held
    expect(saver.saveInProgress).toBe(true);

    type('two ');
    api.releaseHeld();
    await flush();
    await flush();

    expect(saver.saveInProgress).toBe(false);
    expect(saver.dirty).toBe(true); // 'two ' is not on disk yet
    expect(api.calls[0].content).toBe('hello\none ');

    await clock.tick(2000);
    expect(api.calls[1].content).toBe('hello\none two ');
    expect(saver.dirty).toBe(false);
  });

  test('continuous typing still saves by MAX_WAIT', async () => {
    const { clock, api, type } = setup();
    for (let i = 0; i < 20; i++) {
      type('x');
      await clock.tick(1000); // always inside the 2s debounce window
    }
    expect(api.calls.length).toBeGreaterThan(0);
    // First save happened no later than MAX_WAIT after the first keystroke.
    expect(api.calls[0].content.length).toBeLessThanOrEqual('hello\n'.length + MAX_WAIT_MS / 1000 + 1);
  });

  test('a save carries the base hash and adopts the new one', async () => {
    const { clock, api, saver, type } = setup();
    const before = saver.baseHash;
    type('!');
    await clock.tick(2000);
    expect(api.calls[0].baseHash).toBe(before);
    expect(saver.baseHash).toBe(h('hello\n!'));
  });

  test('a timed-out save that actually landed is adopted from the verify read', async () => {
    const { clock, api, saver, type } = setup();
    type('slow');
    api.nextResponse = (content) => {
      api.disk = { ...api.disk, content, hash: h(content), modified: 555, size: 4 };
      const err = new Error('aborted');
      err.name = 'AbortError';
      return err;
    };
    await clock.tick(2000);
    expect(saver.dirty).toBe(false);
    expect(saver.baseMtime).toBe(555);
    expect(saver.baseHash).toBe(h('hello\nslow'));
  });

  test('undoing back to the saved text clears dirty', () => {
    const { doc, saver, type } = setup();
    type('z');
    expect(saver.dirty).toBe(true);
    doc.text = 'hello\n';
    saver.noteDocChanged();
    expect(saver.dirty).toBe(false);
  });
});

describe('drafts', () => {
  test('typing persists a draft; a clean save removes it', async () => {
    const { clock, drafts, saver, type } = setup();
    type('draft me');
    await clock.tick(300);
    expect(drafts.get('org:a.org')?.content).toBe('hello\ndraft me');
    await clock.tick(2000);
    expect(saver.dirty).toBe(false);
    expect(drafts.get('org:a.org')).toBeNull();
  });

  test('a draft against the same base is restored on load', () => {
    const { api, doc, drafts, saver, status } = setup();
    drafts.put({ path: 'org:a.org', baseHash: api.disk.hash, content: 'hello\nrestored', savedAt: 1 });
    doc.text = api.disk.content;
    saver.onLoaded({ ...api.disk });
    expect(doc.text).toBe('hello\nrestored');
    expect(saver.dirty).toBe(true);
    expect(saver.baseHash).toBe(api.disk.hash);
    expect(status.at(-1)).toContain('Restored');
  });

  test('a draft against an older base keeps that base so the save cannot overwrite', () => {
    const { api, doc, drafts, saver } = setup();
    drafts.put({ path: 'org:a.org', baseHash: 'older', content: 'hello\nold draft', savedAt: 1 });
    doc.text = api.disk.content;
    saver.onLoaded({ ...api.disk });
    expect(saver.baseHash).toBe('older');
    expect(saver.baseMtime).toBe(0);
  });

  test('flush persists the draft synchronously and fires a keepalive save', () => {
    const { api, drafts, saver, type } = setup();
    type('bye');
    saver.flush();
    expect(drafts.get('org:a.org')?.content).toBe('hello\nbye');
    expect(api.calls[0].keepalive).toBe(true);
  });
});

describe('conflicts', () => {
  test('a 409 raises a conflict and stops autosave until resolved', async () => {
    const { clock, api, saver, conflicts, type } = setup();
    type('mine');
    api.nextResponse = () => ({ status: 'conflict', reason: 'stale' });
    await clock.tick(2000);
    expect(saver.conflict?.reason).toBe('stale');
    expect(conflicts.length).toBe(1);

    type(' more');
    await clock.tick(5000);
    expect(api.calls.length).toBe(1); // no further attempts while unresolved
  });

  test('dismissing keeps the conflict, pauses autosave, and keeps the draft', async () => {
    const { clock, api, drafts, saver, type } = setup();
    type('mine');
    api.nextResponse = () => ({ status: 'conflict', reason: 'stale' });
    await clock.tick(2000);
    saver.dismissConflict();
    expect(saver.autosavePaused).toBe(true);
    expect(saver.conflict).not.toBeNull();
    expect(drafts.get('org:a.org')?.content).toBe('hello\nmine');
  });

  test('keepMine force-writes without a base', async () => {
    const { clock, api, saver, type } = setup();
    type('mine');
    api.nextResponse = () => ({ status: 'conflict', reason: 'stale' });
    await clock.tick(2000);
    await saver.keepMine();
    expect(api.calls[1].baseHash).toBeNull();
    expect(saver.conflict).toBeNull();
    expect(saver.dirty).toBe(false);
  });

  test('keepTheirs loads the disk version and clears the conflict', async () => {
    const { clock, api, doc, saver, type } = setup();
    type('mine');
    const theirs: FileData = { content: 'theirs\n', path: 'org:a.org', hash: h('theirs\n'), modified: 200, size: 7 };
    api.nextResponse = () => ({ status: 'conflict', reason: 'overlap', theirs });
    await clock.tick(2000);
    await saver.keepTheirs();
    expect(doc.text).toBe('theirs\n');
    expect(saver.baseHash).toBe(h('theirs\n'));
    expect(saver.dirty).toBe(false);
    expect(saver.conflict).toBeNull();
  });
});

describe('external changes', () => {
  test('own-save echo and other files are ignored', async () => {
    const { clock, api, saver, type } = setup();
    type('a');
    await clock.tick(2000);
    saver.onExternalChange({ file: 'org:a.org', hash: saver.baseHash! });
    saver.onExternalChange({ file: 'org:other.org', hash: 'x' });
    await flush();
    expect(api.calls.length).toBe(1);
  });

  test('a clean editor refreshes in place from disk', async () => {
    const { api, doc, saver } = setup();
    api.disk = { ...api.disk, content: 'hello\nfrom emacs\n', hash: h('hello\nfrom emacs\n'), modified: 300 };
    saver.onExternalChange({ file: 'org:a.org', hash: api.disk.hash });
    await flush();
    expect(doc.text).toBe('hello\nfrom emacs\n');
    expect(saver.baseHash).toBe(api.disk.hash);
    expect(saver.dirty).toBe(false);
  });

  test('a dirty editor with autosave on saves so the server can merge', async () => {
    const { api, saver, type } = setup();
    type('mine');
    saver.onExternalChange({ file: 'org:a.org', hash: 'changed-on-disk' });
    await flush();
    expect(api.calls.length).toBe(1);
    expect(api.calls[0].content).toBe('hello\nmine');
  });

  test('a merged response is rebased over keystrokes typed meanwhile', async () => {
    const { clock, api, doc, saver, type } = setup();
    type('mine');
    api.hold = true;
    await clock.tick(2000);
    type('+late');
    api.nextResponse = () => ({
      status: 'merged',
      path: 'org:a.org',
      hash: h('merged'),
      modified: 400,
      size: 6,
      content: 'theirs\nhello\nmine',
    });
    api.releaseHeld();
    await flush();
    await flush();
    expect(doc.text).toBe('theirs\nhello\nmine+late');
    expect(saver.dirty).toBe(true);
    expect(saver.baseHash).toBe(h('merged'));
  });
});

describe('file switching', () => {
  test('a save that completes after switching files leaves the new file alone', async () => {
    const { clock, api, saver, doc, type } = setup();
    type('old');
    api.hold = true;
    await clock.tick(2000);

    const other: FileData = { content: 'B\n', path: 'org:b.org', hash: h('B\n'), modified: 1, size: 2 };
    doc.text = other.content;
    saver.onLoaded(other);
    api.releaseHeld();
    await flush();
    await flush();

    expect(saver.path).toBe('org:b.org');
    expect(saver.baseHash).toBe(h('B\n'));
    expect(saver.dirty).toBe(false);
  });
});
