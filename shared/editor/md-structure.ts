// Markdown structure as the query engine reads it (pkm_bridge/queries/notes.py):
// fenced code, YAML frontmatter, and headings with their GitHub-style anchors.

import type { Line, Text } from '@codemirror/state';
import { YAML_KEY_RE } from './md-frontmatter';

/** A fence line: three or more backticks or tildes, at any indent. */
const FENCE_RE = /^[ \t]*(`{3,}|~{3,})/;
const HEADING_RE = /^(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$/;
const LINK_RE = /\[([^\]]*)\]\([^)]*\)/g;
const SLUG_DELETE_RE = /[\[\]!"#$%&'()*+,./:;<=>?@\\^`{|}~]/g;

/**
 * The lines of `doc` outside fenced code, from line `start` on; fence lines
 * themselves are skipped. A fence closes at a run of the same character at
 * least as long as the one that opened it.
 */
export function* linesOutsideFences(doc: Text, start = 1): Generator<Line> {
  let fence = '';
  for (let n = start; n <= doc.lines; n++) {
    const line = doc.line(n);
    const run = FENCE_RE.exec(line.text)?.[1];
    if (fence) {
      if (run && run[0] === fence[0] && run.length >= fence.length) fence = '';
    } else if (run) {
      fence = run;
    } else {
      yield line;
    }
  }
}

/** Number of the first line after the YAML frontmatter; 1 when there is none. */
function bodyStart(doc: Text): number {
  if (doc.lines < 2 || doc.line(1).text.trim() !== '---' || !YAML_KEY_RE.test(doc.line(2).text)) {
    return 1;
  }
  for (let n = 2; n <= doc.lines; n++) {
    const text = doc.line(n).text.trim();
    if (text === '---' || text === '...') return n + 1;
  }
  return 1; // never closed, so not frontmatter
}

/**
 * GitHub-style anchor for a heading's text: links flattened to their text,
 * lowercased, trimmed, punctuation deleted, then each whitespace character
 * replaced with "-".
 */
export function headingSlug(text: string): string {
  return text
    .replace(LINK_RE, '$1')
    .toLowerCase()
    .trim()
    .replace(SLUG_DELETE_RE, '')
    .replace(/\s/g, '-');
}

/**
 * Number of the line holding the heading whose anchor is `fragment`, or null.
 * A repeated anchor gets -1, -2, … in document order; headings in fenced code
 * or in YAML frontmatter don't count.
 */
export function headingLine(doc: Text, fragment: string): number | null {
  const seen = new Map<string, number>();
  for (const line of linesOutsideFences(doc, bodyStart(doc))) {
    const heading = HEADING_RE.exec(line.text);
    if (!heading) continue;
    const slug = headingSlug(heading[2] ?? '');
    const n = seen.get(slug) ?? 0;
    seen.set(slug, n + 1);
    if ((n ? `${slug}-${n}` : slug) === fragment) return line.number;
  }
  return null;
}
