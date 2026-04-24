import { EditorView, basicSetup } from 'codemirror';
import { markdown } from '@codemirror/lang-markdown';
import { oneDark } from '@codemirror/theme-one-dark';
import { emacs } from '@replit/codemirror-emacs';
import { syntaxHighlighting } from '@codemirror/language';
import { orgMode, orgHighlightStyle } from './org-mode';
import { orgFoldService, autoFoldPropertyDrawers } from './org-fold';
import { orgImageField } from './org-images';
import { orgLinkField, createOrgLinkClickHandler } from './org-links';

/** Create and mount a CodeMirror editor for the given file. */
export function createEditor(
  container: HTMLElement,
  filepath: string,
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
        // Leaves room to scroll the cursor above the on-screen keyboard even
        // when the cursor is near the end of the document.
        paddingBottom: '50vh',
        fontSize: '16px',
      },
      '@media (max-width: 768px)': {
        '.cm-scroller': {
          fontSize: '18px',
        },
      },
    }),
    // Dynamic scroll margin reflecting the on-screen keyboard. CodeMirror uses
    // this to keep the cursor out of the keyboard-obscured region when
    // scrollIntoView runs.
    EditorView.scrollMargins.of(() => ({ bottom: keyboardOverlap() + 20 })),
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
      createOrgLinkClickHandler(),
    );
  }

  const view = new EditorView({
    extensions,
    parent: container,
    doc: '',
  });

  // On mobile, scroll the cursor into view when the editor gains focus.
  // 'nearest' + the dynamic bottom scroll margin above keeps the tapped line
  // where it was unless it would be hidden by the keyboard.
  view.contentDOM.addEventListener('focus', () => {
    if (window.innerWidth < 768) {
      setTimeout(() => {
        const head = view.state.selection.main.head;
        view.dispatch({
          effects: EditorView.scrollIntoView(head, { y: 'nearest' }),
        });
      }, 300);
    }
  });

  // When the virtual keyboard opens (visualViewport shrinks), re-scroll so
  // the cursor is not under the keyboard. The scroll margin above reserves
  // keyboard-sized space at the bottom, so 'nearest' does the right thing.
  if (window.visualViewport) {
    let prevHeight = window.visualViewport.height;
    window.visualViewport.addEventListener('resize', () => {
      const vv = window.visualViewport!;
      if (vv.height < prevHeight && view.hasFocus) {
        requestAnimationFrame(() => {
          const head = view.state.selection.main.head;
          view.dispatch({
            effects: EditorView.scrollIntoView(head, { y: 'nearest' }),
          });
        });
      }
      prevHeight = vv.height;
    });
  }

  return view;
}

function keyboardOverlap(): number {
  if (!window.visualViewport) return 0;
  const vv = window.visualViewport;
  return Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
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
