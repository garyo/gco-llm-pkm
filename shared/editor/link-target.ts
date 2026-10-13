import { STORAGE_KEYS } from './types';

export interface LinkTarget {
  /** Prefixed note path, e.g. "org:pages/x.md". */
  path: string;
  line: number | null;
  /** A heading anchor; see headingLine in md-structure.ts. */
  fragment: string | null;
}

/**
 * Resolve a relative link against the current prefixed path:
 * "org:journals/x.md" + "../pages/Long%20Now.md#setup" gives the path
 * "org:pages/Long Now.md" and the fragment "setup". Path segments and the
 * fragment are percent-decoded, a numeric ::suffix becomes the target line,
 * and a bare "#fragment" stays in the current note. Absolute targets can't be
 * mapped into the org:/logseq: namespaces, so they're skipped.
 */
export function resolveRelativePath(rel: string, currentFilePath: string): LinkTarget | null {
  let line: number | null = null;
  let fragment: string | null = null;

  const sep = rel.indexOf('::');
  if (sep >= 0) {
    const suffix = rel.slice(sep + 2);
    rel = rel.slice(0, sep);
    if (/^\d+$/.test(suffix)) line = parseInt(suffix, 10);
  }
  const hash = rel.indexOf('#');
  if (hash >= 0) {
    fragment = decode(rel.slice(hash + 1)) || null;
    rel = rel.slice(0, hash);
  }

  const colon = currentFilePath.indexOf(':');
  if (rel.startsWith('/') || colon < 0) return null;
  if (!rel) return fragment || line ? { path: currentFilePath, line, fragment } : null;

  const prefix = currentFilePath.slice(0, colon);
  const parts = currentFilePath.slice(colon + 1).split('/').slice(0, -1);
  for (const seg of rel.split('/').map(decode)) {
    if (seg === '' || seg === '.') continue;
    if (seg === '..') {
      if (parts.length === 0) return null; // escapes the prefix root
      parts.pop();
    } else {
      parts.push(seg);
    }
  }
  return { path: `${prefix}:${parts.join('/')}`, line, fragment };
}

/** Percent-decode, keeping text with a malformed escape as written. */
function decode(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

/** Ask the host app to open a note, by prefixed path, at a line or heading anchor. */
export function navigateTo(path: string, line: number | null, fragment: string | null = null): void {
  window.dispatchEvent(
    new CustomEvent('editor:navigate', { detail: { path, line, fragment } })
  );
}

/** Open an external URL in a new tab. */
export function openExternal(url: string): void {
  window.open(url, '_blank');
}

/** Open an /assets/ URL, carrying the auth token the asset route requires. */
export function openAsset(url: string): void {
  const authToken = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
  window.open(url + (authToken ? `?token=${authToken}` : ''), '_blank');
}
