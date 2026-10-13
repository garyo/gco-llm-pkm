import { describe, expect, test } from 'bun:test';
import { EditorState, Text, type Transaction, type TransactionSpec } from '@codemirror/state';
import type { EditorView } from '@codemirror/view';
import { history, undo } from '@codemirror/commands';
import { findQueryBlocks, queryBlocks } from '@pkm/editor/query-blocks';
import { serverText, ViewAdapter } from '@pkm/editor/diff-apply';

/** [opening line, closing line or null] of each block, 1-based. */
function blockLines(...lines: string[]): [number, number | null][] {
  return findQueryBlocks(Text.of(lines)).map((b) => [b.open.number, b.close?.number ?? null]);
}

describe('findQueryBlocks', () => {
  test('a block runs from its query line through the closing line', () => {
    expect(
      blockLines('# Egypt', '<!-- query: Egypt -->', '## [entry](x.md#a)', 'text', '<!-- /query 3f9a2c1e -->', 'after'),
    ).toEqual([[2, 5]]);
  });

  test('a query line with no closing line yet is a block without results', () => {
    expect(
      blockLines('<!-- query: a -->', 'mine', '<!-- query: b -->', '<!-- /query -->', 'mine', '<!-- query: c -->'),
    ).toEqual([
      [1, null],
      [3, 4],
      [6, null],
    ]);
  });

  test('markers inside ``` or ~~~ fences are ignored', () => {
    expect(
      blockLines(
        '```',
        '<!-- query: an example -->',
        '```',
        '~~~~',
        '<!-- /query -->',
        '```', // a different fence character doesn't close it
        '~~~~',
        '<!-- query: real -->',
        '<!-- /query 3f9a2c1e -->',
      ),
    ).toEqual([[8, 9]]);
  });

  test('closing lines need an opening line and the documented syntax', () => {
    expect(blockLines('<!-- /query -->', '<!-- query: a -->', '<!-- /query XYZ -->', '    <!-- /query -->')).toEqual([
      [2, null],
    ]);
  });
});

const PAGE = [
  '# Egypt',
  '<!-- query: Egypt -->',
  '## [2026-10-13 Tue · Travel](../journals/2026-10-13.md#travel)',
  'We saw the pyramids.',
  '<!-- /query 3f9a2c1e -->',
  'My own notes.',
].join('\n');

const at = (text: string) => PAGE.indexOf(text);
const QUERY_LINE_END = at('\n## [');
const CLOSE_LINE_END = at('\nMy own');

function page(doc = PAGE): EditorState {
  return EditorState.create({
    doc,
    extensions: [queryBlocks('org:pages/egypt.md', { onStatus() {}, save: async () => {} }), history()],
  });
}

/** The text after applying `spec` as an ordinary (user) transaction. */
function edit(spec: TransactionSpec, state = page()): string {
  return state.update(spec).state.doc.toString();
}

describe('read-only query results', () => {
  test('typing inside the results is refused', () => {
    expect(edit({ changes: { from: at('pyramids'), insert: 'great ' }, userEvent: 'input.type' })).toBe(PAGE);
  });

  test('so are edits from commands that set no userEvent, like emacs kill-line', () => {
    const line = at('We saw');
    expect(edit({ changes: { from: line, to: line + 'We saw the pyramids.'.length } })).toBe(PAGE);
  });

  test('the query line stays editable', () => {
    expect(edit({ changes: { from: at(' -->\n## ['), insert: ' OR eclipse' } })).toBe(
      PAGE.replace('query: Egypt', 'query: Egypt OR eclipse'),
    );
  });

  test('deleting the whole block is allowed', () => {
    expect(edit({ changes: { from: at('<!-- query'), to: CLOSE_LINE_END } })).toBe('# Egypt\n\nMy own notes.');
  });

  test('deleting part of it, from the query line into the results, is refused', () => {
    expect(edit({ changes: { from: at('Egypt -->'), to: at('the pyramids') } })).toBe(PAGE);
  });

  test('line breaks around the results are protected, but new lines can follow the block', () => {
    expect(edit({ changes: { from: QUERY_LINE_END, insert: '\n' } })).toBe(PAGE); // Enter after the query
    expect(edit({ changes: { from: QUERY_LINE_END, to: QUERY_LINE_END + 1 } })).toBe(PAGE);
    expect(edit({ changes: { from: CLOSE_LINE_END, to: CLOSE_LINE_END + 1 } })).toBe(PAGE);
    expect(edit({ changes: { from: CLOSE_LINE_END, insert: 'x' } })).toBe(PAGE);
    expect(edit({ changes: { from: CLOSE_LINE_END, insert: '\nMore.' } })).toBe(
      PAGE.replace('My own', 'More.\nMy own'),
    );
  });

  test('server text applies anywhere', () => {
    expect(
      edit({ changes: { from: at('pyramids'), to: at('pyramids') + 8, insert: 'temples' }, annotations: serverText }),
    ).toBe(PAGE.replace('pyramids', 'temples'));
  });

  test('a refresh from disk reaches the results through the doc adapter', () => {
    let state = page();
    const view = {
      get state() {
        return state;
      },
      composing: false,
      dispatch(spec: TransactionSpec) {
        state = state.update(spec).state;
      },
    };
    const adapter = new ViewAdapter(() => view as unknown as EditorView);
    adapter.rebase(adapter.checkpoint(), PAGE.replace('pyramids', 'temples'));
    expect(state.doc.toString()).toBe(PAGE.replace('pyramids', 'temples'));
  });

  test('a block without results protects nothing', () => {
    const doc = '<!-- query: Egypt -->\nmine';
    expect(edit({ changes: { from: doc.length, insert: '!' } }, page(doc))).toBe(doc + '!');
  });

  test('undo after a server refresh takes back the user edit, not the refresh', () => {
    let state = page();
    state = state.update({ changes: { from: PAGE.length, insert: ' More.' }, userEvent: 'input.type' }).state;
    const from = state.doc.toString().indexOf('pyramids');
    state = state.update({ changes: { from, to: from + 8, insert: 'temples' }, annotations: serverText }).state;
    undo({ state, dispatch: (tr: Transaction) => (state = tr.state) });
    expect(state.doc.toString()).toBe(PAGE.replace('pyramids', 'temples'));
  });
});
