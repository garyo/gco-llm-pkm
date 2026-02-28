import { foldService, foldEffect } from '@codemirror/language';
import type { EditorView } from '@codemirror/view';

/** Folding service that folds :PROPERTIES: … :END: drawers. */
export const orgFoldService = foldService.of((state, from, _to) => {
  const line = state.doc.lineAt(from);
  if (!line.text.match(/^[\s]*:PROPERTIES:/i)) return null;

  let endLine = line.number + 1;
  while (endLine <= state.doc.lines) {
    const currentLine = state.doc.line(endLine);
    if (currentLine.text.match(/^[\s]*:END:/i)) {
      return { from: line.to, to: currentLine.from };
    }
    endLine++;
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
