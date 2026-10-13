# Todo views and checking off through query blocks

Status: design, decisions made 2026-10-13 (see the end). Builds on
`doc/query-blocks-design.md` (phase 3 there).

## Goal

A ToDo page made of a few queries ("open items this week", "Pelorus todos", "stale items
to triage") where you can tick an item off right on the page, in Emacs, the web editor or
the chat, and the tick lands in the journal the item came from, with the date it was done.
Plus `NOT` and `AND` as readable query keywords.

## What the notes say

Checkboxes in the 389 notes, leaving out machine-written Heartbeat and TickTick Review
sections (413 more open ones live there, and stay excluded):

| | Open | Done |
|---|---|---|
| Journals, last 7 days | 5 | |
| 8–30 days old | 43 | |
| 31–90 days old | 84 | |
| Older than 90 days | 314 | |
| Pages | 48 | |
| All notes | | 590 |

- 249 of the open journal items sit under `# Today`, the daily plans. Most of the 314 old
  ones are plan items that were never ticked.
- 34 open items are nested under other items, 22 carry a `#tag`, 5 carry `{ticktick:ID}`.

So one `is:todo` list over everything would be 446 items, mostly stale. A ToDo page wants
date windows, topic views and a triage view, which is what the additions below provide.

## Query additions

| Write | Meaning |
|---|---|
| `is:todo` | open task items (`- [ ]`) instead of sections |
| `is:done` | checked task items (`- [x]`) |
| `NOT diary`, `NOT (a OR b)` | the same as `-diary`, `-(a OR b)` |
| `a AND b` | the same as `a b`; for readability |
| `before:90d`, `after:30d` | dates relative to today: older than 90 days, within 30 days |
| `sort:oldest` | oldest first (the default is `sort:newest`) |
| `show:list` | task views as one flat checklist, each item followed by its source |

`NOT` and `AND` must be capitals, like `OR`; lowercase `not` and `and` stay plain words.
`is:todo` or `is:done` on its own is a complete query, like `last:7d`.

### Task views

With `is:` the unit becomes the task item: a list item that starts with a checkbox, with
its continuation lines and sub-items. Matching follows the existing rules:

- If a section's heading path matches by itself, all its open (or done) items come in,
  sub-sections included: `Pelorus is:todo` lists everything open under `# Pelorus Nav`.
- Otherwise each item is tested with its heading path: `is:todo #egypt` finds an Egypt-
  tagged item under `# Today`; `text:pelorus is:todo` also finds plain mentions.
- A nested item whose parent is listed comes along with the parent rather than twice. A
  nested item listed on its own (its parent is done) is shifted left to stand alone.
- Items in Heartbeat and TickTick Review sections, and in query blocks, never count.
- Dates: for `is:todo` they are the journal's date (when the item was written down); for
  `is:done` they are the completion date, from the item's ✅ stamp, else the journal's
  date. So `is:done last:7d` is "done this week", old items included.

By default results keep the usual layout, grouped under a linked heading per section:

```markdown
## [2026-10-12 Mon · PKM](../journals/2026-10-12.md#pkm)
- [ ] Auto-updating queries on pages (or anywhere)

## [2026-10-12 Mon · Emacs](../journals/2026-10-12.md#emacs)
- [ ] Try `markdown-ts-appear`: Rendered Markdown, editable in place
```

With `show:list`, one flat checklist, denser for long lists:

```markdown
- [ ] Auto-updating queries on pages (or anywhere) · [2026-10-12 PKM](../journals/2026-10-12.md#pkm)
- [ ] Try `markdown-ts-appear`: Rendered Markdown, editable in place · [2026-10-12 Emacs](../journals/2026-10-12.md#emacs)
```

### Completion stamps

Checking an item off, through a view or directly in a note, appends ` ✅ YYYY-MM-DD` to
its first line (the Obsidian Tasks convention); unchecking it removes the stamp. A stamp is plain text that every editor
shows as written, and the date is today in the server's time zone (or the Mac's, from
Emacs).

## Checking items off

### One operation, run where the files are

All the rules live in one engine function, `check_task`, which works on texts and never
touches files:

1. **Locate** (`locate_task(page_text, page_path, line)`): read the page alone to learn
   which note and section the copied item came from. A grouped item takes the nearest entry
   heading above it, the rule the edit guard already uses; a `show:list` item, the link at
   the end of its line.
2. **Check** (`check_task(page_text, page_path, line, source_text, checked, today)`): find
   the item in that section of the source text, a task line with the same text compared
   without link targets (copies re-point relative links), the n-th copy matching the n-th
   occurrence. Toggle it and add or remove the stamp. Flip the copy the same way in the
   page text, so the page shows the tick at once. Return both new texts and the item.

If the item isn't there, because its source changed since the copy was made, the result
says "this copy is out of date; refresh it" and changes nothing. Nothing is ever guessed
at, and nothing is added to the journals except the stamp: no IDs, no markers.

The callers do the writing, each where its files are:

- **Server** (`/api/checkbox/toggle`, used by the web editor and the chat): reads the page
  and the source through FileEditor, runs `check_task`, and writes the source, then the
  page, each merge-safely against the hash it read. The page write is cosmetic: if it
  loses a race, the next pass fixes the copy.
- **Emacs** (CLI `locate-task`, then `check-task`): runs the same Python locally, so it is
  as quick as `C-c C-c` refresh and works offline. It hands over its buffers' texts, so
  unsaved edits in either note are respected, applies the two texts it gets back to the
  buffers, and saves each buffer that had no unsaved changes before. Going through the
  server instead would wait on Syncthing both ways.

### What happens after a tick

1. The source line is ticked and stamped; the clicked copy shows the same at once.
2. The source change starts the usual refresh pass, about 20 s after the last change. In
   an `is:todo` view the item then drops out; in a full view it stays, ticked.
3. Until that pass, ticking again undoes it. Ticks made in a row keep pushing the pass
   back, so a triage session clears its ticked items together once you pause.

For the copy flip to work everywhere, the closing line's hash ignores checkbox state and
stamps: a copy that differs only in those isn't a hand edit. Hashes written before this
change are accepted alongside the new ones, so existing blocks aren't flagged on the first
pass.

### Per front end

- **Web editor**: a checkbox in results posts to `/api/checkbox/toggle` with the page,
  line and item text instead of being refused; the page comes back over SSE as usual, and
  a refusal shows in the status line.
- **Chat**: no change; its checkboxes already post to that endpoint, which now writes
  through for a copied line instead of answering 409.
- **TickTick**: checking a copied item that carries `{ticktick:ID}` also completes the task
  in TickTick (server only, as ticking it in a journal does today). Unchecking such an item
  is refused, as today.
- **Emacs**: `C-c C-c` on a checkbox line in results checks it off as above; on any other
  line of a block it still refreshes. Like ticking in a journal today, it doesn't complete
  TickTick tasks.
- **Claude**: unchanged; it edits sources rather than copies.

## A ToDo page

```markdown
# This week
<!-- query: is:todo last:7d -->

# This month
<!-- query: is:todo after:30d before:7d show:list -->

# Projects

## Pelorus
<!-- query: Pelorus is:todo -->

## PKM and Emacs
<!-- query: (PKM OR Emacs) is:todo -->

## Long Now Boston
<!-- query: (LNB OR "Long Now") is:todo -->

# Done this week
<!-- query: is:done last:7d show:list -->

# Triage: open for over three months
<!-- query: is:todo before:90d sort:oldest limit:30 show:list -->
```

An item can appear in two views (this week, and Pelorus); ticking it in one updates the
other on the next pass. "Done this week" doubles as an undo list: unticking an item there
reopens it.

## Work

| Part | Changes |
|---|---|
| Engine | task units, `is:`, `NOT`/`AND`, relative dates, `sort:`, `show:list`, stamps, tick-blind hash, `locate_task`, `check_task`, CLI commands |
| Server | `/api/checkbox/toggle` writes through via `check_task`; TickTick completion |
| Web editor | checkbox clicks in results post to the endpoint; refusals in the status line |
| Emacs | `C-c C-c` on a copied checkbox: CLI `locate-task` and `check-task`, apply to buffers, save clean ones |
| Docs | the guide page in the notes, the design doc's interfaces |

Each part gets tests like phase 1's, and an end-to-end run on a scratch copy of the notes
before deploying.

## Decisions (2026-10-13)

1. A ticked item stays visible, ticked, until the next refresh pass, then leaves `is:todo`
   views; ticking again before then undoes it.
2. Both layouts: grouped by section by default, `show:list` for a flat checklist.
3. Every tick gets a ✅ completion stamp, through a view or directly in a note (Emacs
   `C-c C-c`, a click in the web editor or chat); unticking removes it. Like Org's
   `CLOSED` timestamp, so "done this week" sees everything.
4. One `check_task` implementation in the engine, run on the server for the web editor and
   chat and locally through the CLI for Emacs.

## Interfaces (for implementers)

Line numbers are 1-based in the CLI and HTTP, 0-based in the Python API.

### Task lines and stamps

- A task line: `^(?P<indent>[ \t]*)(?:[-*+]|[0-9]{1,9}[.)])[ \t]+\[(?P<state>[ xX])\](?=[ \t]|$)`.
  `x` or `X` is done, a space is open. The task is that line plus the following lines
  indented deeper than it (continuations and sub-items, blank lines between them included).
- A stamp: `[ \t]+✅[ \t]*(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})` at the end of a task's first line
  (trailing whitespace allowed after it).
- Setting a task line's state, everywhere (engine, web editor, Emacs):
  - checked: the state becomes `x` (lowercase); any stamp is removed; then ` ✅ YYYY-MM-DD`
    (today, local date) is appended to the line with trailing spaces and tabs stripped;
  - open: the state becomes a space and any stamp is removed;
  - nothing else on the line changes.
- In `show:list` results each item's first line ends with ` · [DATE HEADING](LINK)`; the
  stamp goes before that suffix. Only the engine writes or reads that layout.

### Engine (`pkm_bridge/queries`, still stdlib-only)

- `set_task_state(line, checked, today) -> str`: the rule above, for one plain task line.
- `locate_task(page_text, page_path, line) -> TaskRef` with `source_path` (root-relative)
  and `section` (anchor, "" for text before the first heading); raises `TaskError`.
- `check_task(page_text, page_path, line, source_text, checked, today) -> TaskChange` with
  `page_text`, `source_path`, `source_text`, `source_line` (0-based) and `item` (the
  task's text); raises `TaskError` (message for the user) when the line isn't a copied
  task or its source item can't be found unambiguously.
- The closing line's hash is computed over the results with every task line's state set
  to open and stamps removed. A recorded hash matching either that or the old raw hash
  isn't a hand edit.

### CLI

- `locate-task --root DIR --path REL --line N`, page text on stdin: prints
  `{"source_path": "journals/2026-10-12.md"}` or `{"error": "…"}`. Exit status 0 either
  way.
- `check-task --root DIR --path REL --line N (--check|--uncheck)`, stdin a JSON object
  `{"page": TEXT, "source": TEXT or null}` (null: read the source from disk): prints
  `{"page": …, "source_path": …, "source": …, "source_line": N, "item": …}` or
  `{"error": "…"}`. Exit status 0 either way. Writes nothing.

### HTTP

`POST /api/checkbox/toggle`, body `{"type": "file", "path": "org:…", "item_text": …,
"line_hint": N, "checked": bool}` as today:

- If the target line is a task copy inside query results (at `line_hint`, else the
  nearest copy within 10 lines whose text contains `item_text`): write through with
  `check_task`, then write the page. Response `{"status": "ok", "source": "org:…",
  "source_line": N}`, or 409 `{"error": "…"}` with the `TaskError` message.
- Otherwise toggle the line in the note as today, with the stamp rule above.
- `{ticktick:ID}` items complete in TickTick when checked, as today, whichever path.
