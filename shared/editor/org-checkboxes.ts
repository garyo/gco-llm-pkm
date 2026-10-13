import { Decoration, WidgetType, EditorView } from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { StateField } from '@codemirror/state';
import type { EditorState as CMEditorState, Line, Text, Range } from '@codemirror/state';
import { minimalChange, type TextChange } from './diff-apply';
import { authHeaders } from './file-api';
import { journalDateStr } from './journal';
import { TASK_RE, setTaskState } from './tasks';

// "- [ ] Title {ticktick:ID}" list items (org and markdown). Clicking the
// checkbox completes the task in TickTick via /api/checkbox/toggle, then
// checks the line off in the document. The {ticktick:...} marker is the
// source of truth and stays in the file; it's just folded out of view.
//
// In markdown files plain task items get a checkbox too, which checks the
// item off with a ✅ completion stamp, or reopens it (see tasks.ts).
const TICKTICK_MARKER_RE = /\s?\{ticktick:([^}\s]+)\}/;

class TicktickCheckboxWidget extends WidgetType {
  /** `taskId` null: a plain task item that toggles locally. */
  constructor(
    private checked: boolean,
    private taskId: string | null,
  ) {
    super();
  }

  toDOM(): HTMLElement {
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = this.checked;
    input.className = 'tt-checkbox';
    if (this.taskId) {
      input.disabled = this.checked;
      input.dataset.taskId = this.taskId;
      input.title = this.checked
        ? 'Completed in TickTick'
        : 'Click to complete this task in TickTick';
    }
    input.style.cssText =
      'cursor: pointer; margin: 0 2px; vertical-align: middle; accent-color: #6cb6ff;';
    return input;
  }

  eq(other: TicktickCheckboxWidget): boolean {
    return this.checked === other.checked && this.taskId === other.taskId;
  }

  ignoreEvent(): boolean {
    return false;
  }
}

function buildCheckboxDecorations(
  doc: Text,
  selection: CMEditorState['selection'],
  plainTasks: boolean,
): DecorationSet {
  const decorations: Range<Decoration>[] = [];
  const cursors = selection.ranges.map((r) => ({ from: r.from, to: r.to }));
  const overlapsCursor = (from: number, to: number) =>
    cursors.some((c) => c.from <= to && c.to >= from);

  for (let lineNum = 1; lineNum <= doc.lines; lineNum++) {
    const line = doc.line(lineNum);
    const task = TASK_RE.exec(line.text);
    if (!task) continue;
    const marker = TICKTICK_MARKER_RE.exec(line.text);
    if (!marker && !plainTasks) continue;

    const taskId = marker ? marker[1] : null;
    const checked = task[1] !== ' ';

    // Replace the "[ ]" token, which ends the match, with a live checkbox widget
    const boxTo = line.from + task[0].length;
    const boxFrom = boxTo - 3;
    if (!overlapsCursor(boxFrom, boxTo)) {
      decorations.push(
        Decoration.replace({
          widget: new TicktickCheckboxWidget(checked, taskId),
        }).range(boxFrom, boxTo)
      );
    }

    if (!marker) continue;
    // Fold the {ticktick:...} marker (and its leading space) out of view
    const markFrom = line.from + (marker.index ?? 0);
    const markTo = markFrom + marker[0].length;
    if (!overlapsCursor(markFrom, markTo)) {
      decorations.push(Decoration.replace({}).range(markFrom, markTo));
    }
  }

  return Decoration.set(decorations, true);
}

/** StateField that renders checkbox lines interactively, revealing the raw
 * text when the cursor enters them. `plainTasks` adds plain task items. */
export function checkboxField(plainTasks: boolean) {
  return StateField.define<DecorationSet>({
    create(state) {
      return buildCheckboxDecorations(state.doc, state.selection, plainTasks);
    },
    update(value, tr) {
      if (tr.docChanged || tr.selection) {
        return buildCheckboxDecorations(tr.state.doc, tr.state.selection, plainTasks);
      }
      return value;
    },
    provide: (f) => EditorView.decorations.from(f),
  });
}

/**
 * Check the task on `line` off, stamped with today's local date, or reopen it,
 * as a user edit. The state and the stamp are edited apart, so a cursor in the
 * task's text stays where it is.
 */
function setTask(view: EditorView, line: Line, checked: boolean): void {
  const next = setTaskState(line.text, checked, journalDateStr());
  const state = line.text.indexOf('[') + 1; // the checkbox is a task line's first "["
  const changes: TextChange[] = [{ from: line.from + state, to: line.from + state + 1, insert: next[state] }];
  const stamp = minimalChange(line.text.slice(state + 1), next.slice(state + 1));
  if (stamp) {
    const offset = line.from + state + 1;
    changes.push({ from: offset + stamp.from, to: offset + stamp.to, insert: stamp.insert });
  }
  view.dispatch({ changes });
}

/** Check off or reopen the task item holding `input`. */
function togglePlainTask(view: EditorView, input: HTMLInputElement): void {
  const line = view.state.doc.lineAt(view.posAtDOM(input));
  const task = TASK_RE.exec(line.text);
  if (task) setTask(view, line, task[1] === ' ');
}

/** Complete the task in TickTick, then check its line off in the document. */
function completeTicktickTask(view: EditorView, input: HTMLInputElement): void {
  const taskId = input.dataset.taskId;
  if (!taskId) return;

  input.disabled = true;
  fetch('/api/checkbox/toggle', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ type: 'ticktick', task_id: taskId, checked: true }),
  })
    .then((res) => {
      if (!res.ok) return res.json().then((d) => Promise.reject(d?.error || res.statusText));
      // Check the source line off; the widget rebuilds as checked+disabled
      // and autosave persists the file.
      const line = view.state.doc.lineAt(view.posAtDOM(input));
      if (TASK_RE.exec(line.text)?.[1] === ' ') setTask(view, line, true);
    })
    .catch((err) => {
      console.error('TickTick checkbox toggle failed:', err);
      input.disabled = false;
      input.checked = false;
      input.title = `Failed to complete in TickTick: ${err}`;
    });
}

/**
 * Whether the editor would accept an edit at `pos`. Change filters refuse
 * some (read-only query results, whose checkboxes query-blocks.ts handles),
 * and a TickTick task shouldn't be completed when its line can't then be
 * marked done.
 */
function canEdit(view: EditorView, pos: number): boolean {
  return !view.state.update({ changes: { from: pos, to: pos + 1, insert: 'x' } }).changes.empty;
}

/** The checkbox widget `event` happened on, if any. */
export function checkboxAt(event: Event): HTMLInputElement | null {
  return (event.target as HTMLElement).closest<HTMLInputElement>('.tt-checkbox');
}

/** Click handler for the checkbox widgets. Uses mousedown (like org links)
 * to act before CodeMirror moves the cursor and unfolds the widget. */
export const checkboxClickHandler = EditorView.domEventHandlers({
  mousedown(event: MouseEvent, view: EditorView) {
    const input = checkboxAt(event);
    if (!input || input.disabled || !canEdit(view, view.posAtDOM(input))) return false;
    event.preventDefault();
    event.stopPropagation();
    if (input.dataset.taskId) completeTicktickTask(view, input);
    else togglePlainTask(view, input);
    return true;
  },
});
