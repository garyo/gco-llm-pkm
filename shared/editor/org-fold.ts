import { foldService, foldEffect } from '@codemirror/language';
import type { EditorView } from '@codemirror/view';

/** Folding service for :PROPERTIES: … :END: drawers and #+begin_… blocks. */
export const orgFoldService = foldService.of((state, from, _to) => {
  const line = state.doc.lineAt(from);

  if (line.text.match(/^[\s]*:PROPERTIES:/i)) {
    for (let n = line.number + 1; n <= state.doc.lines; n++) {
      const currentLine = state.doc.line(n);
      if (currentLine.text.match(/^[\s]*:END:/i)) {
        return { from: line.to, to: currentLine.from };
      }
    }
    return null;
  }

  const begin = line.text.match(/^\s*#\+begin_([a-zA-Z-]+)/i);
  if (begin) {
    const endRe = new RegExp(`^\\s*#\\+end_${begin[1]}\\s*$`, 'i');
    for (let n = line.number + 1; n <= state.doc.lines; n++) {
      const currentLine = state.doc.line(n);
      if (/^\*+\s/.test(currentLine.text)) return null; // headline ends the block
      if (endRe.test(currentLine.text)) {
        return { from: line.to, to: currentLine.to };
      }
    }
  }

  return null;
});

/** Auto-fold all property drawers in the document. */
export function autoFoldPropertyDrawers(view: EditorView): void {
  const effects: ReturnType<typeof foldEffect.of>[] = [];
  const doc = view.state.doc;

  for (let i = 1; i <= doc.lines; i++) {
    const line = doc.line(i);
    if (!line.text.match(/^[\s]*:PROPERTIES:/i)) continue;

    let endLine = i + 1;
    while (endLine <= doc.lines) {
      const currentLine = doc.line(endLine);
      if (currentLine.text.match(/^[\s]*:END:/i)) {
        effects.push(foldEffect.of({ from: line.to, to: currentLine.from }));
        break;
      }
      endLine++;
    }
  }

  if (effects.length > 0) {
    view.dispatch({ effects });
  }
}
