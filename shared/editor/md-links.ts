import { EditorView } from '@codemirror/view';
import { isImageTarget } from './asset-images';
import { navigateTo, openAsset, openExternal, resolveRelativePath } from './link-target';

/** `[label](target)` and `![alt](target)`, targets without whitespace. */
const MD_LINK_RE = /(!?)\[([^\]]*)\]\(([^)\s]+)\)/g;

/**
 * Follow one markdown link target.
 *
 * Unlike org, markdown link syntax is readable as-is, so nothing is folded --
 * only the click behaviour needs supplying.
 */
function openMdLinkTarget(
  target: string,
  event: MouseEvent,
  currentFilePath: string,
): boolean {
  // pandoc writes bare URLs in angle-bracket autolink form
  const inner = target.startsWith('<') && target.endsWith('>') ? target.slice(1, -1) : target;

  if (inner.startsWith('http://') || inner.startsWith('https://')) {
    event.preventDefault();
    event.stopPropagation();
    openExternal(inner);
    return true;
  }

  if (inner.startsWith('/assets/') || (isImageTarget(inner) && inner.includes('assets/'))) {
    event.preventDefault();
    event.stopPropagation();
    const name = inner.slice(inner.lastIndexOf('/') + 1);
    openAsset(`/assets/${name}`);
    return true;
  }

  // A local note: resolve against the file we're viewing.
  const resolved = resolveRelativePath(inner, currentFilePath);
  if (resolved) {
    event.preventDefault();
    event.stopPropagation();
    navigateTo(resolved.path, resolved.line);
    return true;
  }
  return false;
}

/**
 * Click handler for markdown links. Navigation is emitted as a window
 * CustomEvent ('editor:navigate'); each consumer app listens and routes.
 */
export function createMdLinkClickHandler(currentFilePath: string) {
  return EditorView.domEventHandlers({
    mousedown(event: MouseEvent, view: EditorView) {
      // Require Cmd/Ctrl click on desktop, plain tap on touch -- matching org.
      const isTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
      if (!isTouch && !event.metaKey && !event.ctrlKey) return false;

      const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
      if (pos === null) return false;

      const line = view.state.doc.lineAt(pos);
      const lineOffset = pos - line.from;

      MD_LINK_RE.lastIndex = 0;
      let match;
      while ((match = MD_LINK_RE.exec(line.text)) !== null) {
        const start = match.index;
        const end = start + match[0].length;
        if (lineOffset >= start && lineOffset <= end) {
          return openMdLinkTarget(match[3], event, currentFilePath);
        }
      }
      return false;
    },
  });
}
