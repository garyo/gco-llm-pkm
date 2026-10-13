import { describe, expect, test } from 'bun:test';
import { resolveRelativePath } from '@pkm/editor/link-target';

const HERE = 'org:pages/egypt.md';

describe('resolveRelativePath', () => {
  test('resolves against the folder of the current note', () => {
    expect(resolveRelativePath('../journals/2026-10-13.md', HERE)).toEqual({
      path: 'org:journals/2026-10-13.md',
      line: null,
      fragment: null,
    });
    expect(resolveRelativePath('./sub/../other.md', HERE)?.path).toBe('org:pages/other.md');
  });

  test('keeps the #fragment, decoded', () => {
    expect(resolveRelativePath('../journals/2026-10-13.md#travel-egypt', HERE)).toEqual({
      path: 'org:journals/2026-10-13.md',
      line: null,
      fragment: 'travel-egypt',
    });
    expect(resolveRelativePath('cafe.md#caf%C3%A9-notes', HERE)?.fragment).toBe('café-notes');
  });

  test('decodes percent-escapes in the path', () => {
    expect(resolveRelativePath('Long%20Now%20Boston%20Audio%20Setup.md#mixer', HERE)).toEqual({
      path: 'org:pages/Long Now Boston Audio Setup.md',
      line: null,
      fragment: 'mixer',
    });
  });

  test('keeps malformed escapes as written', () => {
    expect(resolveRelativePath('100%.md#50%', HERE)).toEqual({
      path: 'org:pages/100%.md',
      line: null,
      fragment: '50%',
    });
  });

  test('a bare #fragment points into the current note', () => {
    expect(resolveRelativePath('#itinerary', HERE)).toEqual({ path: HERE, line: null, fragment: 'itinerary' });
  });

  test('a numeric ::suffix is a line; other search options are dropped', () => {
    expect(resolveRelativePath('notes.org::42', HERE)).toEqual({ path: 'org:pages/notes.org', line: 42, fragment: null });
    expect(resolveRelativePath('notes.org::*Heading', HERE)?.line).toBeNull();
  });

  test('targets that leave the note roots, or name nothing, resolve to null', () => {
    expect(resolveRelativePath('/etc/hosts', HERE)).toBeNull();
    expect(resolveRelativePath('../../outside.md', HERE)).toBeNull();
    expect(resolveRelativePath('#', HERE)).toBeNull();
    expect(resolveRelativePath('x.md', 'no-prefix.md')).toBeNull();
  });
});
