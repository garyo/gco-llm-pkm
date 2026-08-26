import { Decoration, WidgetType, EditorView } from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { StateField } from '@codemirror/state';
import type { Text, Range } from '@codemirror/state';
import { STORAGE_KEYS } from './types';

export const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp']);

/** True when a link target names an image we can preview. */
export function isImageTarget(name: string): boolean {
  return IMAGE_EXTS.has('.' + (name.split('.').pop()?.toLowerCase() ?? ''));
}

class AssetImageWidget extends WidgetType {
  constructor(
    private url: string,
    private alt: string,
  ) {
    super();
  }

  toDOM(): HTMLElement {
    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'padding: 4px 0; max-width: 400px;';
    wrapper.className = 'asset-image-preview';

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

  eq(other: AssetImageWidget): boolean {
    return this.url === other.url;
  }

  get estimatedHeight(): number {
    return 200;
  }
}

/** Where a syntax puts the filename and the alt text within its match. */
export interface AssetImageSyntax {
  /** A /g regex matching one image link. */
  re: RegExp;
  /** Pull the asset filename out of a match (org and markdown differ in order). */
  filename: (m: RegExpExecArray) => string;
  /** Pull the alt text, if the syntax carries one. */
  alt?: (m: RegExpExecArray) => string | undefined;
}

/**
 * Build a StateField that previews asset images below the line linking them.
 *
 * Assets live in one flat directory, so the filename alone locates them
 * regardless of how the link spells the path.
 */
export function createAssetImageField(syntax: AssetImageSyntax): StateField<DecorationSet> {
  const { re, filename: getName, alt: getAlt } = syntax;

  function build(doc: Text): DecorationSet {
    const widgets: Range<Decoration>[] = [];

    for (let lineNum = 1; lineNum <= doc.lines; lineNum++) {
      const line = doc.line(lineNum);
      let match;
      re.lastIndex = 0;

      while ((match = re.exec(line.text)) !== null) {
        const name = getName(match);
        if (!isImageTarget(name)) continue;

        widgets.push(
          Decoration.widget({
            widget: new AssetImageWidget(`/assets/${name}`, getAlt?.(match) || name),
            block: true,
            side: 1,
          }).range(line.to)
        );
      }
    }

    return Decoration.set(widgets, true);
  }

  return StateField.define<DecorationSet>({
    create(state) {
      return build(state.doc);
    },
    update(value, tr) {
      return tr.docChanged ? build(tr.state.doc) : value;
    },
    provide: (f) => EditorView.decorations.from(f),
  });
}
