import { Decoration, WidgetType, EditorView } from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { StateField } from '@codemirror/state';
import type { EditorState as CMEditorState, Text, Range } from '@codemirror/state';
import { findHeadingId } from './org-images';
import { STORAGE_KEYS } from './types';

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

/** Resolve a relative file: link against the current prefixed path
 * ("org:journals/x.org" + "file:../y.org" -> "org:y.org"). Absolute targets
 * can't be mapped into the org:/logseq: namespaces, so they're skipped.
 * A numeric ::suffix becomes a target line; other ::suffixes are dropped. */
function resolveFileLink(
  target: string,
  currentFilePath: string,
): { path: string; line: number | null } | null {
  let rel = target.slice('file:'.length);
  let line: number | null = null;

  const sep = rel.indexOf('::');
  if (sep >= 0) {
    const suffix = rel.slice(sep + 2);
    rel = rel.slice(0, sep);
    if (/^\d+$/.test(suffix)) line = parseInt(suffix, 10);
  }

  const colon = currentFilePath.indexOf(':');
  if (!rel || rel.startsWith('/') || colon < 0) return null;

  const prefix = currentFilePath.slice(0, colon);
  const parts = currentFilePath.slice(colon + 1).split('/').slice(0, -1);
  for (const seg of rel.split('/')) {
    if (seg === '' || seg === '.') continue;
    if (seg === '..') {
      if (parts.length === 0) return null; // escapes the prefix root
      parts.pop();
    } else {
      parts.push(seg);
    }
  }
  return { path: `${prefix}:${parts.join('/')}`, line };
}

/** Open an org link target. Handles id:, attachment:, http(s):, and file: links. */
function openOrgLinkTarget(
  target: string,
  view: EditorView,
  pos: number,
  event: MouseEvent,
  currentFilePath: string,
): boolean {
  // External URLs
  if (target.startsWith('http://') || target.startsWith('https://')) {
    event.preventDefault();
    event.stopPropagation();
    window.open(target, '_blank');
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

  if (target.startsWith('file:')) {
    const resolved = resolveFileLink(target, currentFilePath);
    if (resolved) {
      event.preventDefault();
      event.stopPropagation();
      window.dispatchEvent(
        new CustomEvent('editor:navigate', {
          detail: { path: resolved.path, line: resolved.line },
        })
      );
      return true;
    }
    return false;
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
          const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
          if (openOrgLinkTarget(target, view, pos ?? 0, event, currentFilePath)) return true;
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
          if (openOrgLinkTarget(match[1], view, pos, event, currentFilePath)) return true;
        }
      }
      return false;
    },
  });
}
