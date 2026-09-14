// Replacing editor text without losing the cursor, folds, or keystrokes typed
// while a request was in flight.

import { ChangeSet, type ChangeSpec } from '@codemirror/state';
import type { EditorView, ViewUpdate } from '@codemirror/view';

export interface TextChange {
  from: number;
  to: number;
  insert: string;
}

/** The single edit that turns `current` into `next`, or null when they are equal. */
export function minimalChange(current: string, next: string): TextChange | null {
  if (current === next) return null;

  let start = 0;
  const maxStart = Math.min(current.length, next.length);
  while (start < maxStart && current.charCodeAt(start) === next.charCodeAt(start)) start++;

  let endCur = current.length;
  let endNext = next.length;
  while (endCur > start && endNext > start && current.charCodeAt(endCur - 1) === next.charCodeAt(endNext - 1)) {
    endCur--;
    endNext--;
  }

  // Never split a surrogate pair at either boundary.
  if (start > 0 && isHighSurrogate(current.charCodeAt(start - 1))) start--;
  if (endCur < current.length && isLowSurrogate(current.charCodeAt(endCur))) {
    endCur++;
    endNext++;
  }

  return { from: start, to: endCur, insert: next.slice(start, endNext) };
}

function isHighSurrogate(code: number): boolean {
  return code >= 0xd800 && code <= 0xdbff;
}

function isLowSurrogate(code: number): boolean {
  return code >= 0xdc00 && code <= 0xdfff;
}

/** A snapshot of the document plus every edit made since it was taken. */
export interface Checkpoint {
  text: string;
  /** Edits since `text`, composed; mapping a `text`-relative change through it targets the live doc. */
  since: ChangeSet;
}

/**
 * How the save controller talks to the editor. Every replacement goes through a
 * checkpoint so text typed after the checkpoint is preserved: the difference
 * between the checkpoint and the new text is mapped over those later edits.
 */
export interface DocAdapter {
  getText(): string;
  isComposing(): boolean;
  checkpoint(): Checkpoint;
  release(checkpoint: Checkpoint): void;
  /** Apply the `checkpoint.text -> text` difference on top of edits made since the checkpoint. */
  rebase(checkpoint: Checkpoint, text: string): void;
}

const COMPOSITION_RETRY_MS = 250;

/** Adapter over a CodeMirror view. Feed `onUpdate` from the view's update listener. */
export class ViewAdapter implements DocAdapter {
  private readonly live = new Set<Checkpoint>();

  constructor(private readonly getView: () => EditorView | null) {}

  onUpdate(update: ViewUpdate): void {
    if (!update.docChanged) return;
    for (const cp of this.live) cp.since = cp.since.compose(update.changes);
  }

  getText(): string {
    return this.getView()?.state.doc.toString() ?? '';
  }

  isComposing(): boolean {
    return this.getView()?.composing ?? false;
  }

  checkpoint(): Checkpoint {
    const text = this.getText();
    const cp = { text, since: ChangeSet.empty(text.length) };
    this.live.add(cp);
    return cp;
  }

  release(checkpoint: Checkpoint): void {
    this.live.delete(checkpoint);
  }

  rebase(checkpoint: Checkpoint, text: string): void {
    const view = this.getView();
    if (!view) {
      this.release(checkpoint);
      return;
    }
    // Replacing text mid-IME-composition corrupts the editor DOM on mobile
    // keyboards; the checkpoint keeps accumulating edits while we wait.
    if (view.composing) {
      setTimeout(() => this.rebase(checkpoint, text), COMPOSITION_RETRY_MS);
      return;
    }
    this.release(checkpoint);
    const change = minimalChange(checkpoint.text, text);
    if (!change) return;
    const changes: ChangeSpec = ChangeSet.of([change], checkpoint.text.length).map(checkpoint.since);
    view.dispatch({ changes });
  }
}
