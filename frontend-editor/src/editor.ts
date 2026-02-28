import { EditorView, basicSetup } from 'codemirror';
import { markdown } from '@codemirror/lang-markdown';
import { oneDark } from '@codemirror/theme-one-dark';
import { emacs } from '@replit/codemirror-emacs';
import { syntaxHighlighting } from '@codemirror/language';
import type { EditorState } from './types';
import { orgMode, orgHighlightStyle } from './org-mode';
import { orgFoldService, autoFoldPropertyDrawers } from './org-fold';
import { orgImageField } from './org-images';
import { orgLinkField, createOrgLinkClickHandler } from './org-links';

/** Create and mount a CodeMirror editor for the given file. */
export function createEditor(
  container: HTMLElement,
  filepath: string,
  state: EditorState,
  onDocChanged: () => void,
): EditorView {
  const isOrg = filepath.endsWith('.org');
  const langMode = isOrg ? orgMode : markdown();

  const extensions = [
    basicSetup,
    langMode,
    oneDark,
    emacs(),
    EditorView.lineWrapping,
    EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        onDocChanged();
      }
    }),
    EditorView.theme({
      '.cm-scroller': {
        paddingBottom: '10vh',
        fontSize: '16px',
      },
      '@media (max-width: 768px)': {
        '.cm-scroller': {
          fontSize: '18px',
        },
      },
    }),
    EditorView.contentAttributes.of({
      autocapitalize: 'on',
      autocorrect: 'on',
      spellcheck: 'true',
    }),
  ];

  if (isOrg) {
    extensions.push(
      syntaxHighlighting(orgHighlightStyle),
      orgFoldService,
      orgImageField,
      orgLinkField,
      createOrgLinkClickHandler(state),
    );
  }

  return new EditorView({
    extensions,
    parent: container,
    doc: '',
  });
}

/** Set editor content and optionally fold property drawers / scroll to line. */
export function setEditorContent(
  view: EditorView,
  content: string,
  filepath: string,
  scrollToLine?: number | null,
): void {
  view.dispatch({
    changes: { from: 0, to: 0, insert: content },
  });

  if (filepath.endsWith('.org')) {
    setTimeout(() => autoFoldPropertyDrawers(view), 50);
  }

  if (scrollToLine != null && scrollToLine >= 1 && scrollToLine <= view.state.doc.lines) {
    setTimeout(() => {
      const line = view.state.doc.line(scrollToLine);
      view.dispatch({
        effects: EditorView.scrollIntoView(line.from, { y: 'center' }),
      });
    }, 100);
  }
}
