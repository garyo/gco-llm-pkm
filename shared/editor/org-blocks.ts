import { Decoration, EditorView } from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { StateField, RangeSetBuilder } from '@codemirror/state';
import type { Extension, Text } from '@codemirror/state';

const BEGIN_RE = /^\s*#\+begin_([a-zA-Z-]+)/i;
const END_RE = /^\s*#\+end_[a-zA-Z-]+/i;
const HEADLINE_RE = /^\*+\s/;

/** Visual family per block type; unlisted types get the generic style. */
const BLOCK_FAMILIES: Record<string, string> = {
  src: 'code',
  example: 'code',
  export: 'code',
  quote: 'quote',
  verse: 'quote',
  center: 'quote',
  comment: 'comment',
};

const decoCache = new Map<string, Decoration>();

function lineDeco(family: string, part: 'delim' | 'body'): Decoration {
  const cls = `cm-org-block cm-org-block-${part} cm-org-block-${family}`;
  let deco = decoCache.get(cls);
  if (!deco) {
    deco = Decoration.line({ class: cls });
    decoCache.set(cls, deco);
  }
  return deco;
}

function buildBlockDecorations(doc: Text): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  let family: string | null = null;

  for (let i = 1; i <= doc.lines; i++) {
    const line = doc.line(i);
    if (family === null) {
      const begin = line.text.match(BEGIN_RE);
      if (begin) {
        family = BLOCK_FAMILIES[begin[1].toLowerCase()] ?? 'generic';
        builder.add(line.from, line.from, lineDeco(family, 'delim'));
      }
    } else if (END_RE.test(line.text)) {
      builder.add(line.from, line.from, lineDeco(family, 'delim'));
      family = null;
    } else if (HEADLINE_RE.test(line.text)) {
      family = null; // a headline implicitly ends an unclosed block
    } else {
      builder.add(line.from, line.from, lineDeco(family, 'body'));
    }
  }

  return builder.finish();
}

const orgBlockField = StateField.define<DecorationSet>({
  create(state) {
    return buildBlockDecorations(state.doc);
  },
  update(value, tr) {
    if (tr.docChanged) {
      return buildBlockDecorations(tr.state.doc);
    }
    return value;
  },
  provide: (f) => EditorView.decorations.from(f),
});

const orgBlockTheme = EditorView.baseTheme({
  '.cm-org-block': {
    backgroundColor: 'rgba(0, 0, 0, 0.2)',
    borderLeft: '3px solid rgba(92, 99, 112, 0.8)',
    paddingLeft: '8px',
  },
  '.cm-org-block-delim': {
    fontSize: '0.75em',
    opacity: '0.8',
  },
  '.cm-org-block-code': {
    borderLeftColor: 'rgba(198, 120, 221, 0.5)',
  },
  '.cm-org-block-quote': {
    borderLeftColor: 'rgba(97, 175, 239, 0.6)',
    backgroundColor: 'rgba(97, 175, 239, 0.07)',
  },
  '.cm-org-block-quote.cm-org-block-body': {
    fontStyle: 'italic',
  },
  '.cm-org-block-comment': {
    borderLeftColor: 'rgba(92, 99, 112, 0.5)',
    backgroundColor: 'rgba(0, 0, 0, 0.12)',
  },
  '.cm-org-block-comment.cm-org-block-body': {
    opacity: '0.6',
  },
});

/** Full-width line styling for #+begin_… / #+end_… blocks. */
export const orgBlocks: Extension = [orgBlockField, orgBlockTheme];
