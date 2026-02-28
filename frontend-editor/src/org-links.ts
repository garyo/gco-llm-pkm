import { Decoration, WidgetType, EditorView } from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { StateField } from '@codemirror/state';
import type { EditorState as CMEditorState, Text, Range } from '@codemirror/state';
import { findHeadingId } from './org-images';
import { STORAGE_KEYS } from './types';
import type { EditorState } from './types';

const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp']);

class OrgLinkWidget extends WidgetType {
  constructor(
    private description: string,
    private target: string,
  ) {
    super();
  }

  toDOM(): HTMLElement {
    const span = document.createElement('span');
    span.textContent = this.description;
    span.style.cssText = 'color: #6cb6ff; text-decoration: underline; cursor: pointer;';
    span.className = 'org-link-folded';
    span.title = this.target;
    return span;
  }

  eq(other: OrgLinkWidget): boolean {
    return this.description === other.description && this.target === other.target;
  }

  ignoreEvent(): boolean {
    return false;
  }
}

function buildOrgLinkDecorations(doc: Text, selection: CMEditorState['selection']): DecorationSet {
  const decorations: Range<Decoration>[] = [];
  const linkRe = /\[\[([^\]]+)\](?:\[([^\]]+)\])?\]/g;
  const cursors = selection.ranges.map((r) => ({ from: r.from, to: r.to }));

  for (let lineNum = 1; lineNum <= doc.lines; lineNum++) {
    const line = doc.line(lineNum);
    let match;
    linkRe.lastIndex = 0;

    while ((match = linkRe.exec(line.text)) !== null) {
      const from = line.from + match.index;
      const to = from + match[0].length;
      const target = match[1];
      const description = match[2] || target;

      const overlaps = cursors.some((c) => c.from <= to && c.to >= from);
      if (overlaps) continue;

      // Skip image attachments
      if (target.startsWith('attachment:')) {
        const ext = '.' + target.split('.').pop()?.toLowerCase();
        if (IMAGE_EXTS.has(ext)) continue;
      }

      decorations.push(
        Decoration.replace({
          widget: new OrgLinkWidget(description, target),
        }).range(from, to)
      );
    }
  }

  return Decoration.set(decorations, true);
}

/** StateField that folds org links, expanding when cursor enters them. */
export const orgLinkField = StateField.define<DecorationSet>({
  create(state) {
    return buildOrgLinkDecorations(state.doc, state.selection);
  },
  update(value, tr) {
    if (tr.docChanged || tr.selection) {
      return buildOrgLinkDecorations(tr.state.doc, tr.state.selection);
    }
    return value;
  },
  provide: (f) => EditorView.decorations.from(f),
});

/** Open an org link target (id: or attachment:). */
function openOrgLinkTarget(
  target: string,
  view: EditorView,
  pos: number,
  event: MouseEvent,
  _state: EditorState,
): boolean {
  if (target.startsWith('id:')) {
    event.preventDefault();
    const uuid = target.slice(3);
    const authToken = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
    const headers: HeadersInit = { 'Content-Type': 'application/json' };
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;

    fetch(`/api/resolve-org-id/${encodeURIComponent(uuid)}`, { headers })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) {
          window.dispatchEvent(
            new CustomEvent('editor:navigate', {
              detail: { path: data.path, line: data.line || null },
            })
          );
        }
      })
      .catch((err) => console.error('Failed to resolve org-id:', err));
    return true;
  }

  if (target.startsWith('attachment:')) {
    const filename = target.slice('attachment:'.length);
    const line = view.state.doc.lineAt(pos);
    const headingId = findHeadingId(view.state.doc, line.number);
    if (headingId) {
      event.preventDefault();
      const authToken = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
      const tokenParam = authToken ? `?token=${authToken}` : '';
      window.open(`/api/org-attachment/${headingId}/${filename}${tokenParam}`, '_blank');
      return true;
    }
  }

  return false;
}

/** Create a click handler for org links. Requires shared state for navigation. */
export function createOrgLinkClickHandler(state: EditorState) {
  return EditorView.domEventHandlers({
    mousedown(event: MouseEvent, view: EditorView) {
      // Folded link widgets: click directly (no modifier needed) since
      // the widget is already styled as a link. We use mousedown to
      // intercept before CodeMirror places the cursor and unfolds the link.
      const widgetEl = (event.target as HTMLElement).closest('.org-link-folded') as HTMLElement;
      if (widgetEl) {
        const target = widgetEl.title;
        if (target) {
          const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
          if (openOrgLinkTarget(target, view, pos ?? 0, event, state)) return true;
        }
      }

      // Expanded (raw) links: require Cmd/Ctrl click (or touch)
      const isTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
      if (!isTouch && !event.metaKey && !event.ctrlKey) return false;

      const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
      if (pos === null) return false;

      const line = view.state.doc.lineAt(pos);
      const lineOffset = pos - line.from;
      const lineText = line.text;

      const linkRe = /\[\[([^\]]+)\](?:\[([^\]]+)\])?\]/g;
      let match;
      while ((match = linkRe.exec(lineText)) !== null) {
        const linkStart = match.index;
        const linkEnd = linkStart + match[0].length;
        if (lineOffset >= linkStart && lineOffset <= linkEnd) {
          if (openOrgLinkTarget(match[1], view, pos, event, state)) return true;
        }
      }
      return false;
    },
  });
}
