import { describe, expect, test } from 'bun:test';
import { Text } from '@codemirror/state';
import { headingLine, headingSlug } from '@pkm/editor/md-structure';

describe('headingSlug', () => {
  test('follows the documented GitHub rule', () => {
    expect(headingSlug('NYC/Virginia trip to meet Ryan')).toBe('nycvirginia-trip-to-meet-ryan');
    expect(headingSlug('[Drone](drone.md)')).toBe('drone');
    expect(headingSlug("Q&A: what's new? (v2)")).toBe('qa-whats-new-v2');
    expect(headingSlug('2026-10-13 Tue · Travel_Egypt')).toBe('2026-10-13-tue-·-travel_egypt');
  });

  test('trims before deleting punctuation, and turns each space into a dash', () => {
    expect(headingSlug('  Budget !  ')).toBe('budget-');
    expect(headingSlug('Two  spaces')).toBe('two--spaces');
  });
});

describe('headingLine', () => {
  const doc = Text.of([
    '---',
    'title: Trip',
    '# a YAML comment',
    '---',
    '# Travel',
    '```',
    '# Travel',
    '```',
    '## Travel ##',
    '#Travel is a tag, not a heading',
    '### [Travel](travel.md)',
    '# Other',
  ]);

  test('finds a heading by its anchor', () => {
    expect(headingLine(doc, 'other')).toBe(12);
  });

  test('repeated anchors get -1, -2 in document order', () => {
    expect(headingLine(doc, 'travel')).toBe(5);
    expect(headingLine(doc, 'travel-1')).toBe(9);
    expect(headingLine(doc, 'travel-2')).toBe(11);
    expect(headingLine(doc, 'travel-3')).toBeNull();
  });

  test('YAML frontmatter holds no headings', () => {
    expect(headingLine(doc, 'a-yaml-comment')).toBeNull();
  });

  test('an unknown anchor gives null', () => {
    expect(headingLine(doc, 'nowhere')).toBeNull();
  });
});
