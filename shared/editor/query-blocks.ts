// Query blocks (doc/query-blocks-design.md): below a `<!-- query: … -->` line
// the server keeps copies of matching journal sections, up to a
// `<!-- /query HASH -->` line. The query line is the user's; the results are a
// read-only copy, shaded, with a Refresh button at the end of the query line.
// Checking a task off in the results checks it off in the note it came from
// (doc/query-todos-design.md).

import { Decoration, EditorView, WidgetType } from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { EditorState, Prec, StateField, Transaction } from '@codemirror/state';
import type { Extension, Line, Range, Text } from '@codemirror/state';
import { authHeaders } from './file-api';
import { linesOutsideFences } from './md-structure';
import { checkboxAt } from './org-checkboxes';
import { TASK_RE, taskText } from './tasks';

const OPEN_RE = /^[ \t]{0,3}<!--\s*query:\s*(.*?)\s*-->\s*$/;
const CLOSE_RE = /^[ \t]{0,3}<!--\s*\/query(?:\s+([0-9a-f]{8}))?\s*-->\s*$/;
const REQUEST_TIMEOUT_MS = 30_000;

export interface QueryBlock {
  /** The `<!-- query: … -->` line. */
  open: Line;
  /** The `<!-- /query … -->` line; null until the server first fills the block in. */
  close: Line | null;
}

/** What the Refresh button and the checkboxes in results need from the app around the editor. */
export interface QueryBlockHost {
  onStatus(message: string, isError?: boolean): void;
  /** Write unsaved edits, so the server works on the note as shown. */
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
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    const body: RefreshResponse | null = await res.json().catch(() => null);
    if (!res.ok) {
      host.onStatus(failure('Query refresh', res.status, body?.error), true);
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

/** The status line for a failed request; `action` says what failed, such as "Query refresh". */
function failure(action: string, status: number, error: string | undefined): string {
  if (status === 401) return `${action} failed: not logged in`;
  if (error) return `${action} failed: ${error}`;
  // What a server without the endpoint answers.
  if (status === 404 || status === 405) return `${action} is not available on this server`;
  return `${action} failed (HTTP ${status})`;
}

interface ToggleResponse {
  /** The note the item was checked off in, such as "org:journals/2026-10-12.md". */
  source?: string;
  error?: string;
}

/**
 * Have the server check the task copied on `line` off in its source note, or
 * reopen it. The server also flips the copy and writes the page, which reaches
 * the editor like any other change on disk. True when that happened.
 */
async function setSourceTask(
  path: string,
  line: Line,
  checked: boolean,
  host: QueryBlockHost,
): Promise<boolean> {
  const action = checked ? 'Check-off' : 'Reopen';
  try {
    // The server finds the copy by its line number in the saved page.
    await host.save();
    const res = await fetch('/api/checkbox/toggle', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        type: 'file',
        path,
        item_text: taskText(line.text),
        line_hint: line.number,
        checked,
      }),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    const body: ToggleResponse | null = await res.json().catch(() => null);
    if (!res.ok) {
      // A 409 says why, such as that the copy is out of date.
      host.onStatus(failure(action, res.status, body?.error), true);
      return false;
    }
    const source = body?.source ? ` in ${body.source.replace(/^\w+:/, '')}` : '';
    host.onStatus(`${checked ? 'Checked off' : 'Reopened'}${source}`);
    return true;
  } catch (e) {
    host.onStatus(`${action} failed: ${(e as Error).message}`, true);
    return false;
  }
}

/** Whether `line` is among a block's results. */
function inResults(blocks: QueryBlock[], line: Line): boolean {
  return blocks.some(
    ({ open, close }) => close !== null && line.number > open.number && line.number < close.number,
  );
}

/**
 * Clicks on the checkboxes in results, which write through to the source
 * instead of editing the copy. Ahead of the ordinary checkbox handler in
 * org-checkboxes.ts.
 */
function resultCheckboxes(path: string, host: QueryBlockHost): Extension {
  return Prec.high(
    EditorView.domEventHandlers({
      mousedown(event, view) {
        const input = checkboxAt(event);
        if (!input) return false;
        const line = view.state.doc.lineAt(view.posAtDOM(input));
        const task = TASK_RE.exec(line.text);
        if (!task || !inResults(view.state.field(blocksField), line)) return false;
        event.preventDefault();
        event.stopPropagation();
        // Disabled: a request in flight, or a TickTick task, which can't be reopened.
        if (input.disabled) return true;
        const checked = task[1] === ' ';
        input.disabled = true;
        void setSourceTask(path, line, checked, host).then((done) => {
          // Until the rewritten page replaces the box, show its new state.
          if (done) input.checked = checked;
          else input.disabled = false;
        });
        return true;
      },
    }),
  );
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

/**
 * Shading, read-only results whose checkboxes write through, and a Refresh
 * button for the query blocks of the note at `path`.
 */
export function queryBlocks(path: string, host: QueryBlockHost): Extension {
  const refreshButton = Decoration.widget({
    widget: new RefreshButton(() => refreshQueries(path, host)),
    side: 1,
  });
  return [
    blocksField,
    readOnlyResults,
    resultCheckboxes(path, host),
    EditorView.decorations.compute([blocksField], (state) =>
      buildDecorations(state.doc, state.field(blocksField), refreshButton),
    ),
    queryTheme,
  ];
}
