// Task lines and their ✅ completion stamps (doc/query-todos-design.md). The
// query engine (set_task_state in pkm_bridge/queries) and Emacs set a task's
// state by the same rule, so all three must agree byte for byte.

/** A task item's first line, through its checkbox; group 1 is the state: ' ', 'x' or 'X'. */
export const TASK_RE = /^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\[([ xX])\](?=[ \t]|$)/;
/** A completion stamp, which ends the line apart from trailing whitespace. */
const STAMP_RE = /[ \t]+✅[ \t]*\d{4}-\d{2}-\d{2}(?=[ \t]*$)/;

/** Task `line` checked, with a ✅ stamp dated `today` (YYYY-MM-DD), or open, without one. */
export function setTaskState(line: string, checked: boolean, today: string): string {
  const task = TASK_RE.exec(line);
  if (!task) throw new Error(`Not a task line: ${line}`);
  const state = task[0].length - 2; // the match ends with the checkbox
  const set = (line.slice(0, state) + (checked ? 'x' : ' ') + line.slice(state + 1)).replace(STAMP_RE, '');
  return checked ? `${set.replace(/[ \t]+$/, '')} ✅ ${today}` : set;
}

/** A task line's text after its checkbox, without a stamp. */
export function taskText(line: string): string {
  const task = TASK_RE.exec(line);
  return line.slice(task ? task[0].length : 0).replace(STAMP_RE, '').trim();
}
