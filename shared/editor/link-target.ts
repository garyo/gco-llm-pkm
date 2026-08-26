import { STORAGE_KEYS } from './types';

/**
 * Resolve a relative link against the current prefixed path
 * ("org:journals/x.org" + "../y.md" -> "org:y.md"). Absolute targets can't be
 * mapped into the org:/logseq: namespaces, so they're skipped. A numeric
 * ::suffix or #Lnn becomes a target line; other fragments are dropped.
 */
export function resolveRelativePath(
  rel: string,
  currentFilePath: string,
): { path: string; line: number | null } | null {
  let line: number | null = null;

  const sep = rel.indexOf('::');
  if (sep >= 0) {
    const suffix = rel.slice(sep + 2);
    rel = rel.slice(0, sep);
    if (/^\d+$/.test(suffix)) line = parseInt(suffix, 10);
  }
  const hash = rel.indexOf('#');
  if (hash >= 0) rel = rel.slice(0, hash);

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

/** Ask the host app to open a note, by prefixed path. */
export function navigateTo(path: string, line: number | null): void {
  window.dispatchEvent(
    new CustomEvent('editor:navigate', { detail: { path, line } })
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
