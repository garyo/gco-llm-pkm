import { Decoration, WidgetType, EditorView } from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { StateField } from '@codemirror/state';
import type { Text, Range } from '@codemirror/state';
import { STORAGE_KEYS } from './types';

const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp']);

/** Walk backward from lineNum to find the enclosing heading's :ID: property. */
export function findHeadingId(doc: Text, lineNum: number): string | null {
  let headingLine = -1;
  for (let i = lineNum; i >= 1; i--) {
    if (/^\*+\s/.test(doc.line(i).text)) {
      headingLine = i;
      break;
    }
  }

  let start = headingLine + 1;
  if (start < 1) start = 1;
  if (start > doc.lines) return null;

  let idx = start;
  while (idx <= doc.lines && !doc.line(idx).text.trim()) idx++;
  if (idx > doc.lines || doc.line(idx).text.trim() !== ':PROPERTIES:') return null;

  idx++;
  while (idx <= doc.lines) {
    const text = doc.line(idx).text.trim();
    if (text === ':END:') break;
    const m = text.match(/^:ID:\s+(.+)/);
    if (m) return m[1].trim();
    idx++;
  }
  return null;
}

class OrgImageWidget extends WidgetType {
  constructor(
    private url: string,
    private alt: string,
  ) {
    super();
  }

  toDOM(): HTMLElement {
    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'padding: 4px 0; max-width: 400px;';
    wrapper.className = 'org-image-preview';

    const img = document.createElement('img');
    const authToken = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
    const tokenParam = authToken ? `?token=${authToken}` : '';
    img.src = this.url + tokenParam;
    img.alt = this.alt;
    img.loading = 'lazy';
    img.style.cssText = 'max-width: 100%; border-radius: 4px; cursor: pointer;';
    img.title = 'Click to open full size';
    img.addEventListener('click', () => window.open(img.src, '_blank'));

    wrapper.appendChild(img);
    return wrapper;
  }

  eq(other: OrgImageWidget): boolean {
    return this.url === other.url;
  }

  get estimatedHeight(): number {
    return 200;
  }
}

function buildOrgImageDecorations(doc: Text): DecorationSet {
  const widgets: Range<Decoration>[] = [];
  const linkRe = /\[\[attachment:([^\]]+)\](?:\[([^\]]+)\])?\]/g;

  for (let lineNum = 1; lineNum <= doc.lines; lineNum++) {
    const line = doc.line(lineNum);
    let match;
    linkRe.lastIndex = 0;

    while ((match = linkRe.exec(line.text)) !== null) {
      const filename = match[1];
      const ext = '.' + filename.split('.').pop()?.toLowerCase();
      if (!IMAGE_EXTS.has(ext)) continue;

      const headingId = findHeadingId(doc, lineNum);
      if (!headingId) continue;

      const url = `/api/org-attachment/${headingId}/${filename}`;
      const alt = match[2] || filename;

      widgets.push(
        Decoration.widget({
          widget: new OrgImageWidget(url, alt),
          block: true,
          side: 1,
        }).range(line.to)
      );
    }
  }

  return Decoration.set(widgets, true);
}

/** StateField providing block image decorations for [[attachment:…]] links. */
export const orgImageField = StateField.define<DecorationSet>({
  create(state) {
    return buildOrgImageDecorations(state.doc);
  },
  update(value, tr) {
    if (tr.docChanged) {
      return buildOrgImageDecorations(tr.state.doc);
    }
    return value;
  },
  provide: (f) => EditorView.decorations.from(f),
});
