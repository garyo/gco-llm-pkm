import { describe, expect, test } from 'bun:test';
import { EditorState, ChangeSet } from '@codemirror/state';
import { minimalChange } from '@pkm/editor/diff-apply';

function apply(current: string, next: string): string {
  const change = minimalChange(current, next);
  if (!change) return current;
  return EditorState.create({ doc: current }).update({ changes: change }).state.doc.toString();
}

describe('minimalChange', () => {
  test('equal strings need no change', () => {
    expect(minimalChange('abc', 'abc')).toBeNull();
  });

  test('append, prepend, and middle edits reproduce the target', () => {
    for (const [a, b] of [
      ['hello', 'hello world'],
      ['world', 'hello world'],
      ['a-b-c', 'a-X-c'],
      ['', 'new'],
      ['gone', ''],
      ['same start same end', 'same start DIFFERENT same end'],
    ]) {
      expect(apply(a, b)).toBe(b);
    }
  });

  test('the change is confined to the differing region', () => {
    const change = minimalChange('prefix MIDDLE suffix', 'prefix other suffix')!;
    expect(change.from).toBe('prefix '.length);
    expect(change.to).toBe('prefix MIDDLE'.length);
    expect(change.insert).toBe('other');
  });

  test('never splits a surrogate pair', () => {
    const a = 'x\u{1F600}y'; // 😀
    const b = 'x\u{1F601}y'; // 😁 — shares the high surrogate
    const change = minimalChange(a, b)!;
    expect(change.from).toBe(1);
    expect(change.to).toBe(3);
    expect(apply(a, b)).toBe(b);
  });

  test('mapping a checkpoint change over later typing keeps both', () => {
    const base = 'line1\nline2\n';
    const merged = 'inserted\nline1\nline2\n'; // someone prepended a line
    const typedSince = ChangeSet.of({ from: base.length, insert: 'typed' }, base.length);
    const change = ChangeSet.of([minimalChange(base, merged)!], base.length).map(typedSince);
    const live = EditorState.create({ doc: base }).update({ changes: typedSince }).state;
    expect(live.update({ changes: change }).state.doc.toString()).toBe('inserted\nline1\nline2\ntyped');
  });
});
