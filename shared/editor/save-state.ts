// Save accounting for one open file: what the server has, what the editor has,
// when to write, and what to do when the file changes underneath us.
//
// Invariants:
// - `savedDoc` is exactly the text the server last confirmed; `dirty` is
//   `getText() !== savedDoc`, never a flag set optimistically.
// - `baseHash` is the hash of the on-disk content our edits are relative to.
// - Nothing typed is ever dropped: a draft is persisted on every change and
//   replacements are rebased over edits made in the meantime.

import type { FileApi, FileData, ConflictReason } from './file-api';
import { isAbortError } from './file-api';
import type { DraftStore } from './draft-store';
import type { DocAdapter } from './diff-apply';
import { STORAGE_KEYS } from './types';

export interface ConflictInfo {
  reason: ConflictReason;
  theirs?: FileData;
  merged?: string;
}

export interface FileChangeEvent {
  /** 'org:rel/path' key, or null when the changed path lies outside the note roots. */
  file: string | null;
  hash?: string;
  mtime?: number;
  deleted?: boolean;
}

export type SaveOutcome = 'saved' | 'merged' | 'unchanged' | 'conflict' | 'failed' | 'skipped';

export interface Clock {
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;
}

export interface AutoSaveSettings {
  enabled: boolean;
  delayMs: number;
}

export interface SaveControllerOptions {
  api: FileApi;
  drafts: DraftStore;
  doc: DocAdapter;
  settings(): AutoSaveSettings;
  onStatus(message: string, isError?: boolean): void;
  onDirtyChange(dirty: boolean): void;
  /** A conflict needs the user's decision (non-null) or has just been resolved (null). */
  onConflict(info: ConflictInfo | null): void;
  clock?: Clock;
}

export const MAX_WAIT_MS = 10_000;
export const DRAFT_DEBOUNCE_MS = 300;
export const KEEPALIVE_LIMIT_CHARS = 60_000;
const STATUS_TICK_MS = 5000;

const realClock: Clock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
};

export function autoSaveSettingsFromStorage(): AutoSaveSettings {
  return {
    enabled: localStorage.getItem(STORAGE_KEYS.AUTO_SAVE_ENABLED) !== 'false',
    delayMs: parseInt(localStorage.getItem(STORAGE_KEYS.AUTO_SAVE_DELAY) || '2000'),
  };
}

export class SaveController {
  path: string | null = null;
  baseHash: string | null = null;
  baseMtime: number | null = null;
  dirty = false;
  saveInProgress = false;
  conflict: ConflictInfo | null = null;
  /** Set when the user dismissed a conflict to keep typing; cleared on resolution. */
  autosavePaused = false;
  lastSaveTime: number | null = null;

  private savedDoc = '';
  private firstDirtyAt: number | null = null;
  private pendingSave = false;
  private autosaveTimer: unknown = null;
  private draftTimer: unknown = null;
  private statusTimer: unknown = null;
  private readonly clock: Clock;

  constructor(private readonly opts: SaveControllerOptions) {
    this.clock = opts.clock ?? realClock;
  }

  // -------------------------------------------------------------------------
  // Loading
  // -------------------------------------------------------------------------

  /** Adopt freshly loaded content. Restores a persisted draft for the file if one exists. */
  onLoaded(data: FileData): void {
    this.clearTimers();
    this.path = data.path;
    this.baseHash = data.hash;
    this.baseMtime = data.modified;
    this.savedDoc = data.content;
    this.lastSaveTime = null;
    this.firstDirtyAt = null;
    this.autosavePaused = false;
    this.setConflict(null);
    this.setDirty(false);

    const draft = this.opts.drafts.get(data.path);
    if (!draft) return;
    if (draft.content === data.content) {
      this.opts.drafts.remove(data.path);
      return;
    }
    this.restoreDraft(draft.content, draft.baseHash, data);
  }

  private restoreDraft(content: string, draftBase: string | null, disk: FileData): void {
    this.opts.doc.rebase(this.opts.doc.checkpoint(), content);
    if (draftBase !== disk.hash) {
      // The draft predates the on-disk version: keep the draft's own base so
      // the next save merges against disk (or reports a conflict) instead of
      // overwriting. baseMtime 0 makes legacy mtime checks fail closed.
      this.baseHash = draftBase;
      this.baseMtime = 0;
    }
    this.noteDocChanged();
    this.opts.onStatus('Restored unsaved changes');
  }

  // -------------------------------------------------------------------------
  // Editing
  // -------------------------------------------------------------------------

  /** The live editor text. */
  text(): string {
    return this.opts.doc.getText();
  }

  /** Call on every document change. */
  noteDocChanged(): void {
    if (!this.path) return;
    const dirty = this.opts.doc.getText() !== this.savedDoc;
    this.setDirty(dirty);
    this.scheduleDraft();
    if (dirty) {
      this.opts.onStatus('Modified (unsaved)');
      this.scheduleAutoSave();
    } else {
      this.clearAutosaveTimer();
    }
  }

  private scheduleAutoSave(): void {
    const settings = this.opts.settings();
    this.clearAutosaveTimer();
    if (!settings.enabled || !this.dirty || this.conflict || this.autosavePaused) return;

    const now = this.clock.now();
    if (this.firstDirtyAt === null) this.firstDirtyAt = now;
    // Trailing debounce, but never later than MAX_WAIT after the first unsaved keystroke.
    const wait = Math.max(0, Math.min(settings.delayMs, this.firstDirtyAt + MAX_WAIT_MS - now));
    this.opts.onStatus(`Modified • Auto-saving in ${Math.ceil(wait / 1000)}s...`);
    this.autosaveTimer = this.clock.setTimeout(() => void this.save(), wait);
  }

  private scheduleDraft(): void {
    if (this.draftTimer) this.clock.clearTimeout(this.draftTimer);
    this.draftTimer = this.clock.setTimeout(() => this.persistDraft(), DRAFT_DEBOUNCE_MS);
  }

  persistDraft(): void {
    if (this.draftTimer) this.clock.clearTimeout(this.draftTimer);
    this.draftTimer = null;
    if (!this.path) return;
    if (!this.dirty) {
      this.opts.drafts.remove(this.path);
      return;
    }
    const result = this.opts.drafts.put({
      path: this.path,
      baseHash: this.baseHash,
      content: this.opts.doc.getText(),
      savedAt: this.clock.now(),
    });
    if (result !== 'stored') {
      this.opts.onStatus('Unsaved text is too large to keep a local draft', true);
    }
  }

  // -------------------------------------------------------------------------
  // Saving
  // -------------------------------------------------------------------------

  async save(opts: { force?: boolean; keepalive?: boolean } = {}): Promise<SaveOutcome> {
    if (!this.path) return 'skipped';
    if (this.saveInProgress) {
      this.pendingSave = true;
      return 'skipped';
    }
    if (this.conflict && !opts.force) {
      this.opts.onStatus('File changed on disk — resolve the conflict to save', true);
      return 'conflict';
    }

    const checkpoint = this.opts.doc.checkpoint();
    const sent = checkpoint.text;
    if (!opts.force && sent === this.savedDoc) {
      this.opts.doc.release(checkpoint);
      return 'unchanged';
    }

    this.saveInProgress = true;
    this.clearAutosaveTimer();
    this.opts.onStatus(opts.force ? 'Saving your version...' : 'Saving...');
    const path = this.path;
    try {
      const result = await this.opts.api.save(path, sent, {
        baseHash: opts.force ? null : this.baseHash,
        baseMtime: opts.force ? null : this.baseMtime,
        keepalive: opts.keepalive,
      });

      if (this.path !== path) {
        this.opts.doc.release(checkpoint);
        return 'saved';
      }
      if (result.status === 'conflict') {
        this.opts.doc.release(checkpoint);
        this.raiseConflict({ reason: result.reason, theirs: result.theirs, merged: result.merged });
        return 'conflict';
      }

      this.adoptSaved(result.hash, result.modified);
      if (result.status === 'merged' && result.content !== undefined) {
        this.savedDoc = result.content;
        this.opts.doc.rebase(checkpoint, result.content);
      } else {
        this.savedDoc = sent;
        this.opts.doc.release(checkpoint);
      }
      this.afterSave(`Saved (${(result.size / 1024).toFixed(1)} KB)`);
      return result.status === 'merged' ? 'merged' : 'saved';
    } catch (e) {
      this.opts.doc.release(checkpoint);
      if (this.path !== path) return 'failed';
      if (isAbortError(e) && (await this.verifySaved(path, sent))) {
        this.afterSave('Saved (response was slow)');
        return 'saved';
      }
      const message = isAbortError(e) ? 'Save timed out' : (e as Error).message;
      this.opts.onStatus(`${message} — your text is kept locally; will retry`, true);
      this.scheduleAutoSave();
      return 'failed';
    } finally {
      this.saveInProgress = false;
      if (this.pendingSave) {
        this.pendingSave = false;
        this.scheduleAutoSave();
      }
    }
  }

  /** After a timeout, check whether the write landed anyway. */
  private async verifySaved(path: string, sent: string): Promise<boolean> {
    try {
      const data = await this.opts.api.load(path, 3000);
      if (data.content !== sent || this.path !== path) return false;
      this.adoptSaved(data.hash, data.modified);
      this.savedDoc = sent;
      return true;
    } catch {
      return false;
    }
  }

  private adoptSaved(hash: string, modified: number): void {
    this.baseHash = hash;
    this.baseMtime = modified;
    this.lastSaveTime = this.clock.now();
    this.firstDirtyAt = null;
    this.autosavePaused = false;
  }

  private afterSave(message: string): void {
    this.setDirty(this.opts.doc.getText() !== this.savedDoc);
    this.persistDraft();
    this.opts.onStatus(message);
    if (this.dirty) {
      // Typed during the round trip; those keystrokes are still unsaved.
      this.scheduleAutoSave();
    } else {
      this.startStatusTicker();
    }
  }

  /** Persist everything we can, synchronously where it matters. Call on pagehide / hidden. */
  flush(): void {
    this.persistDraft();
    if (!this.dirty || this.conflict || this.saveInProgress || !this.opts.settings().enabled) return;
    void this.save({ keepalive: this.opts.doc.getText().length < KEEPALIVE_LIMIT_CHARS });
  }

  // -------------------------------------------------------------------------
  // External changes
  // -------------------------------------------------------------------------

  /** A `file_changed` / `file_deleted` event from the server. */
  onExternalChange(event: FileChangeEvent): void {
    if (!this.path || event.file !== this.path) return;
    if (event.deleted) {
      this.opts.onStatus('This file was deleted on disk', true);
      return;
    }
    if (event.hash === this.baseHash) return; // our own write echoed back, or a no-op rewrite
    if (this.saveInProgress) return; // the response carries the new hash
    if (this.dirty) void this.rebaseOnDisk();
    else void this.refreshFromDisk();
  }

  /** Re-check the file after the page resumes; same rules as an event. */
  async checkDisk(): Promise<void> {
    if (!this.path || this.saveInProgress) return;
    try {
      const data = await this.opts.api.load(this.path, 5000);
      this.onExternalChange({ file: data.path, hash: data.hash });
    } catch {
      // Offline or slow; the next event or save will catch up.
    }
  }

  private async refreshFromDisk(): Promise<void> {
    const path = this.path;
    if (!path) return;
    const checkpoint = this.opts.doc.checkpoint();
    try {
      const data = await this.opts.api.load(path, 5000);
      if (this.path !== path || data.hash === this.baseHash || this.dirty) {
        this.opts.doc.release(checkpoint);
        return;
      }
      this.baseHash = data.hash;
      this.baseMtime = data.modified;
      this.savedDoc = data.content;
      this.opts.doc.rebase(checkpoint, data.content);
      this.setDirty(false);
      this.opts.onStatus(`Refreshed ${data.path.split('/').pop()}`);
    } catch (e) {
      this.opts.doc.release(checkpoint);
      console.error('Failed to refresh file after change:', e);
    }
  }

  /** The file changed while we have unsaved edits: bring theirs in underneath ours. */
  private async rebaseOnDisk(): Promise<void> {
    const path = this.path;
    if (!path || this.conflict) return;
    if (this.opts.settings().enabled) {
      // Saving merges on the server and hands back the combined text.
      await this.save();
      return;
    }
    const checkpoint = this.opts.doc.checkpoint();
    try {
      const result = await this.opts.api.merge(path, checkpoint.text, this.baseHash);
      if (this.path !== path) {
        this.opts.doc.release(checkpoint);
        return;
      }
      if (result.status === 'conflict') {
        this.opts.doc.release(checkpoint);
        this.raiseConflict({ reason: result.reason, theirs: result.theirs, merged: result.merged });
        return;
      }
      // Merged text is disk's version plus our edits; disk is now our base.
      this.baseHash = result.hash;
      this.baseMtime = result.modified;
      this.opts.doc.rebase(checkpoint, result.content ?? checkpoint.text);
      this.opts.onStatus('Merged changes from disk into your unsaved edits');
    } catch {
      this.opts.doc.release(checkpoint);
      this.raiseConflict({ reason: 'stale' });
    }
  }

  // -------------------------------------------------------------------------
  // Conflicts
  // -------------------------------------------------------------------------

  private raiseConflict(info: ConflictInfo): void {
    this.clearAutosaveTimer();
    this.setConflict(info);
    this.opts.onStatus('File changed on disk — conflicts need resolution', true);
  }

  /** The user closed the conflict dialog without deciding: keep editing, keep the draft, stop writing. */
  dismissConflict(): void {
    if (!this.conflict) return;
    this.autosavePaused = true;
    this.persistDraft();
    this.opts.onStatus('Autosave paused until the conflict is resolved', true);
  }

  async keepMine(): Promise<SaveOutcome> {
    this.setConflict(null);
    this.autosavePaused = false;
    return this.save({ force: true });
  }

  async keepTheirs(): Promise<void> {
    const path = this.path;
    if (!path) return;
    const checkpoint = this.opts.doc.checkpoint();
    try {
      const data = this.conflict?.theirs ?? (await this.opts.api.load(path));
      if (this.path !== path) {
        this.opts.doc.release(checkpoint);
        return;
      }
      this.baseHash = data.hash;
      this.baseMtime = data.modified;
      this.savedDoc = data.content;
      this.opts.doc.rebase(checkpoint, data.content);
      this.setConflict(null);
      this.autosavePaused = false;
      this.setDirty(this.opts.doc.getText() !== this.savedDoc);
      this.persistDraft();
      this.opts.onStatus('Loaded the version from disk');
    } catch (e) {
      this.opts.doc.release(checkpoint);
      this.opts.onStatus(`Could not load the file: ${(e as Error).message}`, true);
    }
  }

  /** Load the conflict-marked merge so the user can resolve it by hand; autosave stays off until they save. */
  editMerged(): void {
    const info = this.conflict;
    if (!info?.merged || !info.theirs) return;
    this.baseHash = info.theirs.hash;
    this.baseMtime = info.theirs.modified;
    this.savedDoc = info.theirs.content;
    this.opts.doc.rebase(this.opts.doc.checkpoint(), info.merged);
    this.setConflict(null);
    this.autosavePaused = true;
    this.setDirty(true);
    this.persistDraft();
    this.opts.onStatus('Resolve the <<<<<<< / >>>>>>> markers, then Save');
  }

  // -------------------------------------------------------------------------
  // Lifecycle
  // -------------------------------------------------------------------------

  /** Flush on backgrounding and warn before unloading dirty. */
  attachLifecycle(): void {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') this.flush();
    });
    window.addEventListener('pagehide', () => this.flush());
    window.addEventListener('beforeunload', (e) => {
      if (!this.dirty) return;
      e.preventDefault();
      e.returnValue = '';
    });
  }

  dispose(): void {
    this.clearTimers();
    this.path = null;
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private setDirty(dirty: boolean): void {
    if (dirty === this.dirty) return;
    this.dirty = dirty;
    this.opts.onDirtyChange(dirty);
  }

  private setConflict(info: ConflictInfo | null): void {
    if (info === null && this.conflict === null) return;
    this.conflict = info;
    this.opts.onConflict(info);
  }

  private startStatusTicker(): void {
    this.clearStatusTicker();
    this.statusTimer = this.clock.setTimeout(() => {
      if (this.dirty || this.conflict || this.lastSaveTime === null) return;
      const seconds = Math.floor((this.clock.now() - this.lastSaveTime) / 1000);
      this.opts.onStatus(seconds < 60 ? `Saved ${seconds}s ago` : `Saved ${Math.floor(seconds / 60)}m ago`);
      this.startStatusTicker();
    }, STATUS_TICK_MS);
  }

  private clearAutosaveTimer(): void {
    if (this.autosaveTimer) this.clock.clearTimeout(this.autosaveTimer);
    this.autosaveTimer = null;
  }

  private clearStatusTicker(): void {
    if (this.statusTimer) this.clock.clearTimeout(this.statusTimer);
    this.statusTimer = null;
  }

  private clearTimers(): void {
    this.clearAutosaveTimer();
    this.clearStatusTicker();
    if (this.draftTimer) this.clock.clearTimeout(this.draftTimer);
    this.draftTimer = null;
  }
}
