import { describe, expect, test } from 'bun:test';
import {
  findJournalForDate,
  journalDateStr,
  journalPath,
  journalTemplate,
} from '@pkm/editor/journal';

function file(name: string, dir = 'org', type = 'journal') {
  return { full_path: `${dir}:journals/${name}`, name, dir, type };
}

describe('journalPath', () => {
  test('creates markdown, never org', () => {
    expect(journalPath('2026-09-21')).toBe('org:journals/2026-09-21.md');
  });
});

describe('journalTemplate', () => {
  test('matches the frontmatter pkm_bridge/journal.py writes', () => {
    expect(journalTemplate('2026-09-21', 'ABC-123')).toBe(
      '---\ntitle: "2026-09-21"\nid: ABC-123\ndate: 2026-09-21\n---\n\n',
    );
  });
});

describe('journalDateStr', () => {
  test('formats the local date, zero-padded', () => {
    expect(journalDateStr(new Date(2026, 8, 7))).toBe('2026-09-07');
  });
});

describe('findJournalForDate', () => {
  test('finds a markdown journal in the org dir', () => {
    const files = [file('2026-09-20.md'), file('2026-09-21.md')];
    expect(findJournalForDate(files, '2026-09-21')?.name).toBe('2026-09-21.md');
  });

  test('prefers markdown when a day has both formats', () => {
    // The regression: the .org was created beside an existing .md and then
    // collected scheduled-task content, splitting the day across two files.
    const files = [file('2026-09-21.org'), file('2026-09-21.md')];
    expect(findJournalForDate(files, '2026-09-21')?.name).toBe('2026-09-21.md');
  });

  test('prefers markdown regardless of list order', () => {
    const files = [file('2026-09-21.md'), file('2026-09-21.org')];
    expect(findJournalForDate(files, '2026-09-21')?.name).toBe('2026-09-21.md');
  });

  test('still finds an org-only journal from before the conversion', () => {
    const files = [file('2024-03-04.org')];
    expect(findJournalForDate(files, '2024-03-04')?.name).toBe('2024-03-04.org');
  });

  test('finds a logseq journal by its underscore name', () => {
    const files = [file('2026_09_21.md', 'logseq')];
    expect(findJournalForDate(files, '2026-09-21')?.full_path).toBe('logseq:journals/2026_09_21.md');
  });

  test('ignores non-journal files that mention the date', () => {
    const files = [file('meeting 2026-09-21.md', 'org', 'page')];
    expect(findJournalForDate(files, '2026-09-21')).toBeUndefined();
  });

  test('returns undefined when the day has no entry', () => {
    expect(findJournalForDate([file('2026-09-20.md')], '2026-09-21')).toBeUndefined();
  });
});
