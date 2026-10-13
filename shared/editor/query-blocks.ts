// Query blocks (doc/query-blocks-design.md): below a `<!-- query: … -->` line
// the server keeps copies of matching journal sections, up to a
// `<!-- /query HASH -->` line. The query line is the user's; the results are a
// read-only copy, shaded, with a Refresh button at the end of the query line.

import { Decoration, EditorView, WidgetType } from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { EditorState, StateField, Transaction } from '@codemirror/state';
import type { Extension, Line, Range, Text } from '@codemirror/state';
import { authHeaders } from './file-api';
import { linesOutsideFences } from './md-structure';

const OPEN_RE = /^[ \t]{0,3}<!--\s*query:\s*(.*?)\s*-->\s*$/;
const CLOSE_RE = /^[ \t]{0,3}<!--\s*\/query(?:\s+([0-9a-f]{8}))?\s*-->\s*$/;
const REFRESH_TIMEOUT_MS = 30_000;

export interface QueryBlock {
  /** The `<!-- query: … -->` line. */
  open: Line;
  /** The `<!-- /query … -->` line; null until the server first fills the block in. */
  close: Line | null;
}

/** What the Refresh button needs from the app around the editor. */
export interface QueryBlockHost {
  onStatus(message: string, isError?: boolean): void;
  /** Write unsaved edits, so the server runs the query as typed. */
  save(): Promise<unknown>;
}

/**
 * The query blocks in `doc`, found the way the server finds them: markers in
 * fenced code don't count, and a query line with no closing line before the
 * next query line (or the end) is a block with no results yet.
 */
export function findQueryBlocks(doc: Text): QueryBlock[] {
  const blocks: QueryBlock[] = [];
  let open: Line | null = null;
  for (const line of linesOutsideFences(doc)) {
    if (OPEN_RE.test(line.text)) {
      if (open) blocks.push({ open, close: null });
      open = line;
    } else if (open && CLOSE_RE.test(line.text)) {
      blocks.push({ open, close: line });
      open = null;
    }
  }
  if (open) blocks.push({ open, close: null });
  return blocks;
}

/**
 * Whether replacing `from`..`to` with `inserted` would change a block's
 * results: the lines after its query line through its closing line, and the
 * line breaks on either side. A line break typed at the end of the query line
 * would add a line to the results; one typed at the end of the closing line
 * starts a new line below the block. Deleting the whole block is fine.
 */
function touchesResults({ open, close }: QueryBlock, from: number, to: number, inserted: Text): boolean {
  if (!close || (from <= open.from && to >= close.to)) return false;
  const above = to < open.to || (to === open.to && inserted.lines === 1);
  const below =
    from > close.to || (from === close.to && to === from && inserted.sliceString(0, 1) === '\n');
  return !above && !below;
}

const blocksField = StateField.define<QueryBlock[]>({
  create: (state) => findQueryBlocks(state.doc),
  update: (blocks, tr) => (tr.docChanged ? findQueryBlocks(tr.state.doc) : blocks),
});

/**
 * Refuse edits that touch results. Server text (`serverText` in diff-apply.ts)
 * passes; everything else is the user's, including commands that set no
 * userEvent, such as the emacs kill and yank commands and checkbox clicks.
 * Undo and redo skip change filters, but they only replay the user's own
 * edits, which this filter kept out of the results.
 */
const readOnlyResults = EditorState.changeFilter.of((tr) => {
  if (!tr.docChanged || tr.annotation(Transaction.remote)) return true;
  const blocks = tr.startState.field(blocksField);
  let allowed = true;
  tr.changes.iterChanges((from, to, _fromB, _toB, inserted) => {
    if (blocks.some((b) => touchesResults(b, from, to, inserted))) allowed = false;
  });
  return allowed;
});

const queryLine = Decoration.line({ class: 'cm-query-line' });
const resultLine = Decoration.line({ class: 'cm-query-result' });

function buildDecorations(doc: Text, blocks: QueryBlock[], refreshButton: Decoration): DecorationSet {
  const ranges: Range<Decoration>[] = [];
  for (const { open, close } of blocks) {
    ranges.push(queryLine.range(open.from), refreshButton.range(open.to));
    const last = close?.number ?? open.number;
    for (let n = open.number + 1; n <= last; n++) ranges.push(resultLine.range(doc.line(n).from));
  }
  return Decoration.set(ranges, true);
}

class RefreshButton extends WidgetType {
  constructor(private readonly refresh: () => Promise<void>) {
    super();
  }

  toDOM(): HTMLElement {
    const button = document.createElement('button');
    button.className = 'cm-query-refresh';
    button.textContent = '↻';
    button.title = 'Refresh query results';
    button.setAttribute('aria-label', button.title);
    // Leave the cursor and focus where they are.
    button.addEventListener('mousedown', (e) => e.preventDefault());
    button.addEventListener('click', async () => {
      button.disabled = true;
      await this.refresh();
      button.disabled = false;
    });
    return button;
  }
}

interface RefreshResponse {
  pages?: { status: 'saved' | 'merged' | 'unchanged'; blocks?: { error?: string | null }[] }[];
  error?: string;
}

/**
 * Save, then have the server re-run the note's queries. Rewritten results
 * reach the editor like any other change on disk.
 */
async function refreshQueries(path: string, host: QueryBlockHost): Promise<void> {
  try {
    await host.save();
    host.onStatus('Refreshing query results...');
    const res = await fetch('/api/queries/refresh', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ path }),
      signal: AbortSignal.timeout(REFRESH_TIMEOUT_MS),
    });
    const body: RefreshResponse | null = await res.json().catch(() => null);
    if (!res.ok) {
      host.onStatus(refreshFailure(res.status, body?.error), true);
      return;
    }
    const page = body?.pages?.[0];
    const error = page?.blocks?.find((b) => b.error)?.error;
    if (error) host.onStatus(`Query error: ${error}`, true);
    else if (!page) host.onStatus('No query blocks to refresh');
    else host.onStatus(page.status === 'unchanged' ? 'Query results are up to date' : 'Query results updated');
  } catch (e) {
    host.onStatus(`Query refresh failed: ${(e as Error).message}`, true);
  }
}

function refreshFailure(status: number, error: string | undefined): string {
  if (status === 401) return 'Query refresh failed: not logged in';
  if (error) return `Query refresh failed: ${error}`;
  // What a server without the endpoint answers.
  if (status === 404 || status === 405) return 'Query refresh is not available on this server';
  return `Query refresh failed (HTTP ${status})`;
}

const queryTheme = EditorView.baseTheme({
  // Qualified with .cm-line to outrank the theme's active-line background,
  // which would otherwise leave the cursor's line unshaded.
  '.cm-line.cm-query-line': {
    backgroundColor: 'rgba(97, 175, 239, 0.1)',
    boxShadow: 'inset 2px 0 rgba(97, 175, 239, 0.6)',
  },
  '.cm-line.cm-query-result': {
    backgroundColor: 'rgba(0, 0, 0, 0.18)',
    boxShadow: 'inset 2px 0 rgba(97, 175, 239, 0.3)',
  },
  '.cm-query-refresh': {
    marginLeft: '0.6em',
    padding: '0 0.4em',
    font: 'inherit',
    fontSize: '0.85em',
    color: '#61afef',
    backgroundColor: 'transparent',
    border: '1px solid rgba(97, 175, 239, 0.4)',
    borderRadius: '4px',
    cursor: 'pointer',
  },
  '.cm-query-refresh:hover': {
    backgroundColor: 'rgba(97, 175, 239, 0.15)',
  },
  '.cm-query-refresh:disabled': {
    opacity: '0.5',
    cursor: 'progress',
  },
});

/** Shading, read-only results and a Refresh button for the query blocks of the note at `path`. */
export function queryBlocks(path: string, host: QueryBlockHost): Extension {
  const refreshButton = Decoration.widget({
    widget: new RefreshButton(() => refreshQueries(path, host)),
    side: 1,
  });
  return [
    blocksField,
    readOnlyResults,
    EditorView.decorations.compute([blocksField], (state) =>
      buildDecorations(state.doc, state.field(blocksField), refreshButton),
    ),
    queryTheme,
  ];
}
