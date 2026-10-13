import { GlobalRegistrator } from '@happy-dom/global-registrator';
import { afterAll, afterEach, beforeAll, describe, expect, mock, spyOn, test } from 'bun:test';
import { undo } from '@codemirror/commands';
import type { EditorView } from '@codemirror/view';
import { createEditor, setEditorContent } from '@pkm/editor/createEditor';
import { journalDateStr } from '@pkm/editor/journal';
import { STORAGE_KEYS } from '@pkm/editor/types';

// Clicks go through an editor made by createEditor, so the checkbox widgets,
// their click handlers and the read-only results work as they do in the apps.
beforeAll(() => GlobalRegistrator.register());
afterAll(() => GlobalRegistrator.unregister());

const PATH = 'org:pages/todo.md';
const PAGE = [
  '# Todo',
  '- [ ] Water the plants',
  '- [ ] Book flights {ticktick:def456}',
  '<!-- query: is:todo OR is:done -->',
  '## [2026-10-12 Mon · PKM](../journals/2026-10-12.md#pkm)',
  '- [ ] Auto-updating queries',
  '- [x] Ship query blocks ✅ 2026-10-12',
  '- [ ] Call Bob {ticktick:abc123}',
  '<!-- /query 3f9a2c1e -->',
].join('\n');
const CHECKED_OFF = { status: 'ok', source: 'org:journals/2026-10-12.md', source_line: 4 };

let view: EditorView;
let status: { message: string; isError: boolean }[];
const save = mock(async () => 'unchanged');

function openPage(): void {
  status = [];
  view = createEditor(document.body, PATH, () => {}, {
    onStatus: (message, isError) => {
      status.push({ message, isError: !!isError });
    },
    save,
  });
  setEditorContent(view, PAGE, PATH);
}

afterEach(() => {
  view.destroy();
  save.mockClear();
  mock.restore();
  localStorage.clear();
});

/** The checkbox on the line holding `text`. */
function box(text: string): HTMLInputElement {
  const found = [...view.dom.querySelectorAll<HTMLInputElement>('.tt-checkbox')].find((input) =>
    view.state.doc.lineAt(view.posAtDOM(input)).text.includes(text),
  );
  if (!found) throw new Error(`No checkbox on a line holding "${text}"`);
  return found;
}

function click(input: HTMLInputElement): void {
  input.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
}

function respond(code: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status: code, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(response: () => Promise<Response>) {
  return spyOn(globalThis, 'fetch').mockImplementation(response);
}

/** The JSON body of the first request. */
function sent(fetchMock: ReturnType<typeof mockFetch>): unknown {
  return JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
}

const settle = () => Bun.sleep(1);

describe('checkboxes outside query results', () => {
  test('a click checks the task off with a stamp, and another reopens it', () => {
    const fetchMock = mockFetch(async () => respond(500, {}));
    openPage();
    click(box('Water the plants'));
    expect(view.state.doc.line(2).text).toBe(`- [x] Water the plants ✅ ${journalDateStr()}`);
    click(box('Water the plants'));
    expect(view.state.doc.toString()).toBe(PAGE);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test('a tick is an ordinary edit, which undo takes back', () => {
    openPage();
    click(box('Water the plants'));
    undo(view);
    expect(view.state.doc.toString()).toBe(PAGE);
  });

  test('a TickTick task is completed there, then checked off with a stamp', async () => {
    const fetchMock = mockFetch(async () => respond(200, { status: 'ok' }));
    openPage();
    click(box('Book flights'));
    await settle();
    expect(sent(fetchMock)).toEqual({ type: 'ticktick', task_id: 'def456', checked: true });
    expect(view.state.doc.line(3).text).toBe(`- [x] Book flights {ticktick:def456} ✅ ${journalDateStr()}`);
  });
});

describe('checkboxes in query results', () => {
  test('a click asks the server to check the source off, and leaves the page to it', async () => {
    localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, 'secret');
    const fetchMock = mockFetch(async () => respond(200, CHECKED_OFF));
    openPage();
    const input = box('Auto-updating queries');
    click(input);
    await settle();

    expect(save).toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/checkbox/toggle');
    expect(init?.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer secret' });
    expect(sent(fetchMock)).toEqual({
      type: 'file',
      path: PATH,
      item_text: 'Auto-updating queries',
      line_hint: 6,
      checked: true,
    });
    expect(view.state.doc.toString()).toBe(PAGE);
    expect(status.at(-1)).toEqual({ message: 'Checked off in journals/2026-10-12.md', isError: false });
    // Ticked, and not clickable again until the server's page arrives.
    expect([input.checked, input.disabled]).toEqual([true, true]);
  });

  test('a done copy is reopened by its text without the stamp', async () => {
    const fetchMock = mockFetch(async () => respond(200, CHECKED_OFF));
    openPage();
    click(box('Ship query blocks'));
    await settle();
    expect(sent(fetchMock)).toMatchObject({ item_text: 'Ship query blocks', line_hint: 7, checked: false });
    expect(status.at(-1)?.message).toBe('Reopened in journals/2026-10-12.md');
  });

  test('TickTick copies go to the server the same way', async () => {
    const fetchMock = mockFetch(async () => respond(200, CHECKED_OFF));
    openPage();
    click(box('Call Bob'));
    await settle();
    expect(sent(fetchMock)).toMatchObject({
      type: 'file',
      item_text: 'Call Bob {ticktick:abc123}',
      line_hint: 8,
    });
    expect(view.state.doc.toString()).toBe(PAGE);
  });

  test("a 409 shows the server's reason, and the box can be clicked again", async () => {
    mockFetch(async () => respond(409, { error: 'this copy is out of date; refresh it' }));
    openPage();
    const input = box('Auto-updating queries');
    click(input);
    await settle();
    expect(status.at(-1)).toEqual({
      message: 'Check-off failed: this copy is out of date; refresh it',
      isError: true,
    });
    expect([input.checked, input.disabled]).toEqual([false, false]);
    expect(view.state.doc.toString()).toBe(PAGE);
  });

  test('other failures, such as an expired login, are reported', async () => {
    mockFetch(async () => respond(401, { error: 'Unauthorized' }));
    openPage();
    click(box('Ship query blocks'));
    await settle();
    expect(status.at(-1)).toEqual({ message: 'Reopen failed: not logged in', isError: true });
  });

  test('a second click while the request is in flight sends nothing', async () => {
    const fetchMock = mockFetch(async () => respond(200, CHECKED_OFF));
    openPage();
    const input = box('Auto-updating queries');
    click(input);
    click(input);
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(view.state.doc.toString()).toBe(PAGE);
  });
});
