import { Decoration, WidgetType, EditorView } from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { StateField } from '@codemirror/state';
import type { EditorState as CMEditorState, Text, Range } from '@codemirror/state';
import { IMAGE_EXTS } from './asset-images';
import { navigateTo, openExternal, resolveRelativePath } from './link-target';
import { STORAGE_KEYS } from './types';

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

      // Skip asset images -- orgImageField renders those as block widgets
      if (/^file:(?:\.\.\/)*assets\//.test(target)) {
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

/** Open an org link target. Handles id:, http(s):, and file: links. */
function openOrgLinkTarget(
  target: string,
  event: MouseEvent,
  currentFilePath: string,
): boolean {
  // External URLs
  if (target.startsWith('http://') || target.startsWith('https://')) {
    event.preventDefault();
    event.stopPropagation();
    openExternal(target);
    return true;
  }

  if (target.startsWith('id:')) {
    event.preventDefault();
    const uuid = target.slice(3);
    const authToken = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
    const headers: HeadersInit = { 'Content-Type': 'application/json' };
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;

    fetch(`/api/resolve-org-id/${encodeURIComponent(uuid)}`, { headers })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) navigateTo(data.path, data.line || null);
      })
      .catch((err) => console.error('Failed to resolve org-id:', err));
    return true;
  }

  if (target.startsWith('file:')) {
    const resolved = resolveRelativePath(target.slice('file:'.length), currentFilePath);
    if (resolved) {
      event.preventDefault();
      event.stopPropagation();
      navigateTo(resolved.path, resolved.line);
      return true;
    }
    return false;
  }

  return false;
}

/**
 * Click handler for org links. Navigation is emitted as a window CustomEvent
 * ('editor:navigate'); each consumer app listens and routes.
 */
export function createOrgLinkClickHandler(currentFilePath: string) {
  return EditorView.domEventHandlers({
    mousedown(event: MouseEvent, view: EditorView) {
      // Folded link widgets: click directly (no modifier needed) since
      // the widget is already styled as a link. We use mousedown to
      // intercept before CodeMirror places the cursor and unfolds the link.
      const widgetEl = (event.target as HTMLElement).closest('.org-link-folded') as HTMLElement;
      if (widgetEl) {
        const target = widgetEl.title;
        if (target) {
          if (openOrgLinkTarget(target, event, currentFilePath)) return true;
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
          if (openOrgLinkTarget(match[1], event, currentFilePath)) return true;
        }
      }
      return false;
    },
  });
}
