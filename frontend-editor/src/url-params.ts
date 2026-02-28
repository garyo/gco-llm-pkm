import * as api from './api';

export interface UrlParams {
  file: string | null;
  id: string | null;
  line: number | null;
}

/** Parse URL parameters (?file=, ?id=, ?line=). */
export function parseUrlParams(): UrlParams {
  const params = new URLSearchParams(window.location.search);
  return {
    file: params.get('file'),
    id: params.get('id'),
    line: params.has('line') ? parseInt(params.get('line')!, 10) : null,
  };
}

/** Update the URL with the current file (using replaceState). */
export function updateUrl(file: string, line?: number): void {
  const url = new URL(window.location.href);
  url.searchParams.set('file', file);
  url.searchParams.delete('id');
  if (line != null) {
    url.searchParams.set('line', String(line));
  } else {
    url.searchParams.delete('line');
  }
  history.replaceState(null, '', url.toString());
}

/** Resolve URL params to a file path and optional line number. */
export async function resolveUrlParams(
  params: UrlParams,
): Promise<{ path: string; line: number | null } | null> {
  if (params.file) {
    return { path: params.file, line: params.line };
  }

  if (params.id) {
    const result = await api.resolveOrgId(params.id);
    if (result) {
      return { path: result.path, line: result.line ?? params.line };
    }
  }

  return null;
}
