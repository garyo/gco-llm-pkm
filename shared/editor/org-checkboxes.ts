import { Decoration, WidgetType, EditorView } from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { StateField } from '@codemirror/state';
import type { EditorState as CMEditorState, Text, Range } from '@codemirror/state';
import { STORAGE_KEYS } from './types';

// "- [ ] Title {ticktick:ID}" list items (org and markdown). Clicking the
// checkbox completes the task in TickTick via /api/checkbox/toggle, then
// marks the line [X] in the document. The {ticktick:...} marker is the
// source of truth and stays in the file; it's just folded out of view.
//
// In markdown files plain "- [ ]" task items get a checkbox too, which just
// toggles the [ ] / [x] text.
const CHECKBOX_RE = /^(\s*)- \[( |X|x)\] /;
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
    const box = CHECKBOX_RE.exec(line.text);
    if (!box) continue;
    const marker = TICKTICK_MARKER_RE.exec(line.text);
    if (!marker && !plainTasks) continue;

    const taskId = marker ? marker[1] : null;
    const checked = box[2] !== ' ';

    // Replace the "[ ]" token with a live checkbox widget
    const boxFrom = line.from + box[1].length + 2;
    const boxTo = boxFrom + 3;
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

/** Flip the [ ] / [x] of the task item holding `input`. */
function togglePlainTask(view: EditorView, input: HTMLInputElement): void {
  const line = view.state.doc.lineAt(view.posAtDOM(input));
  const m = CHECKBOX_RE.exec(line.text);
  if (!m) return;
  const pos = line.from + m[1].length + 3;
  view.dispatch({ changes: { from: pos, to: pos + 1, insert: m[2] === ' ' ? 'x' : ' ' } });
}

/** Complete the task in TickTick, then mark the line [X] in the document. */
function completeTicktickTask(view: EditorView, input: HTMLInputElement): void {
  const taskId = input.dataset.taskId;
  if (!taskId) return;

  input.disabled = true;
  const authToken = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;

  fetch('/api/checkbox/toggle', {
    method: 'POST',
    headers,
    body: JSON.stringify({ type: 'ticktick', task_id: taskId, checked: true }),
  })
    .then((res) => {
      if (!res.ok) return res.json().then((d) => Promise.reject(d?.error || res.statusText));
      // Mark the source line [X]; the widget rebuilds as checked+disabled
      // and autosave persists the file.
      const pos = view.posAtDOM(input);
      const line = view.state.doc.lineAt(pos);
      const m = CHECKBOX_RE.exec(line.text);
      if (m && m[2] === ' ') {
        const spacePos = line.from + m[1].length + 3;
        view.dispatch({ changes: { from: spacePos, to: spacePos + 1, insert: 'X' } });
      }
    })
    .catch((err) => {
      console.error('TickTick checkbox toggle failed:', err);
      input.disabled = false;
      input.checked = false;
      input.title = `Failed to complete in TickTick: ${err}`;
    });
}

/** Click handler for the checkbox widgets. Uses mousedown (like org links)
 * to act before CodeMirror moves the cursor and unfolds the widget. */
export const checkboxClickHandler = EditorView.domEventHandlers({
  mousedown(event: MouseEvent, view: EditorView) {
    const input = (event.target as HTMLElement).closest('.tt-checkbox') as HTMLInputElement | null;
    if (!input || input.disabled) return false;
    event.preventDefault();
    event.stopPropagation();
    if (input.dataset.taskId) completeTicktickTask(view, input);
    else togglePlainTask(view, input);
    return true;
  },
});
