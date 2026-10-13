import { describe, expect, test } from 'bun:test';
import { setTaskState, taskText } from '@pkm/editor/tasks';

const check = (line: string) => setTaskState(line, true, '2026-10-13');
const reopen = (line: string) => setTaskState(line, false, '2026-10-13');

describe('setTaskState', () => {
  test('checking sets a lowercase x and appends a stamp', () => {
    expect(check('- [ ] Buy milk')).toBe('- [x] Buy milk ✅ 2026-10-13');
  });

  test('reopening clears the box and removes the stamp', () => {
    expect(reopen('- [x] Buy milk ✅ 2026-10-01')).toBe('- [ ] Buy milk');
  });

  test('checking replaces an existing stamp', () => {
    expect(check('- [x] Buy milk ✅ 2026-10-01')).toBe('- [x] Buy milk ✅ 2026-10-13');
  });

  test('trailing whitespace goes before a new stamp, and stays when one is removed', () => {
    expect(check('- [ ] Buy milk \t ')).toBe('- [x] Buy milk ✅ 2026-10-13');
    expect(check('- [x] Buy milk ✅ 2026-10-01  ')).toBe('- [x] Buy milk ✅ 2026-10-13');
    expect(reopen('- [x] Buy milk ✅ 2026-10-01  ')).toBe('- [ ] Buy milk  ');
  });

  test('an uppercase X is done too', () => {
    expect(check('- [X] Buy milk')).toBe('- [x] Buy milk ✅ 2026-10-13');
    expect(reopen('- [X] Buy milk')).toBe('- [ ] Buy milk');
  });

  test('indented, starred and ordered items', () => {
    expect(check('    - [ ] nested')).toBe('    - [x] nested ✅ 2026-10-13');
    expect(check('\t* [ ] starred')).toBe('\t* [x] starred ✅ 2026-10-13');
    expect(check('12. [ ] ordered')).toBe('12. [x] ordered ✅ 2026-10-13');
    expect(reopen('  3)\t[x] ordered ✅ 2026-10-01')).toBe('  3)\t[ ] ordered');
  });

  test('a stamp preceded by a tab, or with no space before its date', () => {
    expect(reopen('- [x] Buy milk\t✅ 2026-10-01')).toBe('- [ ] Buy milk');
    expect(check('- [x] Buy milk\t✅2026-10-01')).toBe('- [x] Buy milk ✅ 2026-10-13');
  });

  test('only a stamp that ends the line counts', () => {
    expect(reopen('- [x] Pay ✅ 2026-10-01 rent')).toBe('- [ ] Pay ✅ 2026-10-01 rent');
  });

  test('the rest of the line stays as written', () => {
    const line = '- [ ] Read [the  doc](../pages/x.md) #pkm {ticktick:abc}';
    expect(check(line)).toBe(line.replace('[ ]', '[x]') + ' ✅ 2026-10-13');
  });

  test('anything but a task line is an error', () => {
    for (const line of ['- [-] Buy milk', '-[ ] Buy milk', '- [ ]Buy milk', 'Buy milk']) {
      expect(() => check(line)).toThrow();
    }
  });
});

describe('taskText', () => {
  test('is the text after the checkbox, without the stamp', () => {
    expect(taskText('- [x] Buy *milk*  ✅ 2026-10-01 ')).toBe('Buy *milk*');
    expect(taskText('1. [ ] Call Bob {ticktick:abc}')).toBe('Call Bob {ticktick:abc}');
  });
});
