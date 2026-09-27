// Locating and creating the journal entry for a date, shared by both editor
// consumers (the standalone SPA and the chat-app's embedded editor).
//
// Journals are markdown. A day's entry is usually created server-side by the
// journal_append tool (pkm_bridge/journal.py); the editor creating one is the
// fallback for a day nothing has written to yet. Because the created path
// matches the one the tool writes, the create_only save is idempotent: if the entry already exists —
// including when the editor's file list is stale and has not seen it — the
// server reports it and the editor opens it instead of writing a second file.

/** A file list entry, narrowed to the fields journal lookup needs. */
export interface JournalCandidate {
  full_path: string;
  name: string;
  dir: string;
  type: string;
}

/** Local calendar date as YYYY-MM-DD. */
export function journalDateStr(now: Date = new Date()): string {
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

/** Path the editor creates a journal at. Must match pkm_bridge/journal.py. */
export function journalPath(dateStr: string): string {
  return `org:journals/${dateStr}.md`;
}

/** Markdown journal template. Must match pkm_bridge/journal.py. */
export function journalTemplate(dateStr: string, id: string): string {
  return `---\ntitle: "${dateStr}"\nid: ${id}\ndate: ${dateStr}\n---\n\n`;
}

/**
 * Find an existing journal entry for a date, or undefined.
 *
 * Markdown wins over org: days predating the org-to-markdown conversion can
 * have both, and the markdown one is where new content goes.
 */
export function findJournalForDate<T extends JournalCandidate>(
  files: T[],
  dateStr: string,
): T | undefined {
  const logseqDateStr = dateStr.replace(/-/g, '_');
  const matches = files.filter(
    (f) =>
      f.type === 'journal' &&
      (f.name.includes(dateStr) || f.name.includes(logseqDateStr)),
  );

  return matches.find((f) => f.name.endsWith('.md')) ?? matches[0];
}
