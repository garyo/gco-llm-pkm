# Query blocks: live journal views on any page

Status: decisions made 2026-10-13 (see the end); implementation in progress.

## Goal

You write a journal section anywhere, say `# Travel Egypt` in today's journal, and it
appears on the topic page under a query you put there once, with a link back to where it
came from. Pages stay plain Markdown files, so Emacs, the web editor, Syncthing and Claude
keep working on files as they do now. The journal stays the only master copy; what the
page shows is a read-only copy. Transclusion (editing in either place) is out of scope,
but the format leaves room for it (see Phases).

## What the notes say

A throwaway prototype ran candidate queries over the 361 Markdown journals:

| Query | Headings only | Headings and all text mentions |
|---|---|---|
| `Egypt OR eclipse` | 1 entry, 7 lines | 7 entries, 14 lines |
| `Pelorus` | 32 entries, 273 lines | 72 entries, 317 lines |
| `Travel` | 63 entries, 436 lines | 94 entries, 478 lines |
| `Morningside` | 58 entries, 571 lines | 67 entries, 580 lines |
| `LNB OR "Long Now"` | 85 entries, 1,144 lines | 217 entries, 1,310 lines |

- Plain mentions in body text are noisy for established topics (40 extra Pelorus
  entries), so by default only headings and `#tags` count, and `text:` opts in to plain
  mentions. Existing Egypt material is mostly plain mentions, so the Egypt page uses
  `text:` to collect it; new entries can use a heading or a `#Egypt` tag.
- Established topics are big: every LNB section adds up to over 1,100 lines, so there is
  a limit (50 newest) and a compact mode.
- Parsing all 389 notes takes about 40 ms. The server can simply re-run every query
  whenever anything changes, with no index or dependency tracking.

## The format

```markdown
# Journal

<!-- query: Egypt OR eclipse -->
<!-- /query -->
```

You write the query line (the closing line is optional; the server adds it). The server
fills the space between the markers and keeps it current. Everything between the markers
belongs to the server.

An HTML comment is invisible when rendered (grip, the xwidget preview, GitHub), survives
pandoc, can't be confused with anything you would write, and matches the existing
`<!-- backlinks -->` blocks that the prompts already describe as machine-maintained.

Markers inside fenced code blocks are ignored, so notes that describe the syntax don't
run.

## The query language

One line, search-engine style:

| You write | Meaning |
|---|---|
| `Egypt` | a heading containing the word, or a `#Egypt` tag in the text (case-insensitive, whole word) |
| `#Egypt` | the same as `Egypt` |
| `text:Egypt` | the word anywhere, plain mentions included |
| `heading:Pelorus` | headings only |
| `"Long Now"` | a phrase (in headings; with `text:`, anywhere) |
| `Travel Egypt` | both |
| `Egypt OR eclipse` | either; `OR` binds tighter than the implicit AND |
| `-diary` | not |
| `(Travel Egypt) OR eclipse` | grouping |
| `in:pages`, `in:all` | scope; the default is `in:journals` |
| `last:90d`, `after:2026-01-01`, `before:2026-07-01` | date window, by journal date (`d`, `w`, `m`, `y`) |
| `limit:20` | maximum entries, newest first; the default is 50 |
| `show:titles` | a list of links to the entries, no content |

Generic headings don't count as topics: a bare word from a small stop list (*today,
tonight, tomorrow, yesterday, misc, notes, todo, tasks*) never matches a heading. It can
still match a tag, `text:`, or an explicit `heading:`.

Reserved for later phases: `is:todo`, `links:this` (backlinks).

### What counts as a match

A journal splits into sections at its headings, and each section has a heading path such
as `LNB meeting › Agenda`.

1. If the query holds for a section's heading path alone, the whole section comes in,
   sub-sections included. This is the `# Travel Egypt` case.
2. Otherwise each block in the section's own text (a paragraph, or a top-level list item
   with its sub-items) is tested together with the heading path, and the blocks that match
   come in. So `Travel Egypt` matches a `#Egypt` paragraph under `# Travel`: *Travel* comes
   from the heading, *Egypt* from the tag.
3. `heading:` terms only look at heading paths; `text:` terms also look at plain words in
   the block.

Never matched or copied:
- `Heartbeat` and `TickTick Review` sections. They are machine-written, and the heartbeat
  appends its `## Heartbeat` under whichever section comes last, so a copied section can
  contain one (the prototype hit this on 2026-10-09).
- Other query blocks, so pages can't feed each other or loop.
- The note that holds the query, hidden directories, and Syncthing conflict copies.

## What gets written

This is the block the prototype produced for the Egypt page from today's notes (long lines
trimmed, the hash is a placeholder):

```markdown
<!-- query: text:Egypt OR text:eclipse -->
## [2026-10-13 Tue · Travel/Wedding planning](journals/2026-10-13.md#travelwedding-planning)
We just talked about our 2027 plans: since I'm going to be in Egypt end of July through Aug 5 …

## [2026-10-06 Tue · Misc](journals/2026-10-06.md#misc)
Consider better infrastructure to surface your org pages: … (pcons, egypt-eclipse-2027, music pages, etc.)

## [2026-07-22 Wed · Finance](journals/2026-07-22.md#finance)
Adjust Boldin budget (per mo): … No new camera lens (don't need it til next year for Egypt) …

## [2026-07-21 Tue · To Do](journals/2026-07-21.md#to-do)
- [x] Get travel insurance for Egypt

## [2026-06-23 Tue · Travel](journals/2026-06-23.md#travel)
- Created org page for Egypt Eclipse Trip 2027 (Luxor, Astro-Trails): …
  - Itinerary section needs to be filled in with tour day-by-day details

## [2026-05-21 Thu · Camera Lenses](journals/2026-05-21.md#camera-lenses)
- I'd also want a long lens like the Tamron 70-300 … Don't need that til Egypt.

## [2026-04-19 Sun · Eclipse Trip 2027: camera thoughts](journals/2026-04-19.md#eclipse-trip-2027-camera-thoughts)
- Rent this lens: <https://www.lensrentals.com/…> – $77/week …
  - Weight: around 1.5 lbs, not bad.
  …
<!-- /query 3f9a2c1e -->
```

- Each entry is a heading one level below the heading the query sits under (level 2 if
  there is none). It links to the source section by its GitHub-style anchor, the same
  anchors the Emacs block references already use. Entries are headings rather than block
  quotes so they fold in markdown-ts-mode and read like the rest of the page. Links inside
  a source heading are flattened to their text, since links can't nest.
- Content is copied verbatim, except:
  - sub-headings shift down to nest under the entry;
  - relative links and images are re-pointed so they still work from the page's folder
    (`../pages/openfx.md` in a journal becomes `openfx.md` on a page in `pages/`);
  - machine-written sub-sections and nested query blocks are dropped.
- Newest first. Past the limit, a last line says how many more entries were left out. If
  nothing matches, one *No matching entries* line shows that the query ran; a malformed
  query gets a *Query error* line instead.
- The output depends only on the query and the text of the notes (no timestamps, no
  mtimes), so a re-run changes nothing unless the notes changed. That keeps Syncthing
  quiet.
- The closing marker carries a short hash of what the server wrote, so the server can tell
  when someone edited the copy by hand (see below).

## Keeping it current

The server is the only automatic writer.

- **On change.** The web server already watches the note folders, Syncthing deliveries
  from Emacs included, to push live updates to the editor. A refresher subscribes to those
  same events, waits for edits to settle (about 20 s, at most about 2 min during continuous
  typing), re-runs every query, and rewrites only the pages whose results changed. It
  writes through `rewrite_note`, the same three-way-merged path `edit_note` uses, so edits
  you make to the rest of the page are never overwritten.
- **Nightly, and at startup.** A full pass, for date windows like `last:90d` and as a
  safety net.
- **On demand.** `POST /api/queries/refresh`, behind a Refresh button in the web editor,
  and `C-c C-c` in Emacs (see Clients).
- Automatic writes only happen in production (on unless `DEBUG`, overridable with
  `QUERY_REFRESH`), so a dev server can't race it through Syncthing.
- Loops can't happen: query blocks are excluded from the input, so the server's own write
  triggers one more pass, which finds nothing to do.

Refresh-on-open becomes unnecessary. The web editor shows a page's new results about 20 s
after a journal save, because it already reloads files that change on disk. Emacs should
see them in under a minute (Syncthing both ways plus the settle time), via auto-revert.

One rough edge: if a page is open in Emacs **with unsaved edits** when its results change,
Emacs asks whether you really want to edit a buffer whose file changed on disk. Nothing is
lost whichever way you answer, and if Emacs later saves an older copy of the block, the
next pass replaces it.

## Edits inside a block

They are discarded, but they shouldn't vanish silently:

- Both editors make the block read-only, and `edit_note` refuses to change text inside
  one, directing Claude to the source instead.
- If the hash shows the block was edited anyway (another editor, a shell write), the
  server still regenerates it, but first sends an ntfy push that identifies the page. The
  edited version stays in the server's version store for 7 days.

## Avoiding duplicates

Without care, every copied entry shows up twice in every search. Each consumer drops text
inside query blocks:

- the RAG chunker. The same helper skips `<!-- backlinks -->` lists, which it embedded
  until now: the drone page's first chunk was 352 tokens of backlinks.
- `search_notes` and `find_context`, by filtering hits on line ranges;
- the Emacs context sidebar.

Your own `consult-ripgrep` searches will still find the copies, because rg can't skip
line ranges. They sit under dated entry headings in a topic page, so they are easy to tell
apart.

## Clients

### Web editor

- Shade the block and make it read-only. Server rewrites keep merging in as they do now.
- A Refresh button on the query line.
- Follow `#heading` anchors in links. Until now the editor dropped every `#` fragment and
  opened the note at the top, which also affected the block-reference links Emacs creates.

### Emacs (`gco-pkm-query.el`)

- Shade the results and make them read-only, re-applied after auto-revert. RET on an
  entry heading opens the source as it does now.
- `C-c C-c` on a query refreshes it in the buffer, like an org dynamic block, by piping
  the buffer through the engine's command-line entry point. The engine is stdlib-only
  Python shared with the server, so this also works offline.
- `gco-pkm-insert-query` adds a `# Journal` heading and a query seeded from the page
  title.
- Context sidebar: hits inside result blocks are dropped, and a page hit appears when a
  heading's words appear in a page's query or title. You type `# Travel Egypt`, the
  sidebar shows *Egypt Eclipse Trip 2027*, and RET lands on its results.

### Claude

- Both system prompts learn what query blocks are, that they hold copies, and how to add
  one. "Add a journal view to my Egypt page" then just works: Claude writes the query line
  and the server fills it in.
- Claude reading a page reads its block too. That helps for small blocks and costs tokens
  for big ones; limits and `show:titles` keep it in check.

## Phases

1. **Server**: engine, refresher, API, duplicate exclusions, the `edit_note` guard,
   prompts. Useful on its own: Emacs gets results through Syncthing with no Emacs changes.
2. **Editors**: web read-only shading, Refresh, `#anchor` navigation; `gco-pkm-query.el`
   and the sidebar changes.
3. **Write-through**: a checkbox clicked in a block toggles the original, which makes an
   `is:todo` view worth having; jump from any result line to its source line;
   `links:this` backlink queries, like Logseq's linked references.
4. **Maybe later**: general edits inside a block written back to the source through the
   same three-way merge (transclusion-lite). Every entry already records its source and
   the hash shows whether it was edited, so the format wouldn't need to change.

## Alternatives considered

- **Render results on screen only**, as Logseq and Obsidian's Dataview do. No Syncthing
  traffic and no copies in grep, but every reader needs a renderer (a widget in the web
  editor, org-transclusion-style insertion in Emacs that strips the text before each
  save), plain viewers and Claude see only the query, and overlays and widgets are a
  steady source of bugs. The syntax is the same either way, so switching later is cheap.
- **Results in a separate generated file** such as `.pkm/views/<page>.md`: the results
  aren't on the page unless each editor splices them in, which is the on-screen option
  again.
- **An index with dependency tracking**: not needed when a full pass takes 40 ms.

## Decisions (2026-10-13)

1. Results live in the file: any editor works, export works, and there are no overlays or
   widgets to debug.
2. Bare words match headings (minus generic ones like *today*) and `#tags`; `text:` opts
   in to plain mentions.
3. The Logseq archive is out of scope; everything that matters has been converted.
4. 50 newest entries by default; easy to change if it gets unwieldy.

## Interfaces (for implementers)

### Markers

- Opening line: `^[ \t]{0,3}<!--\s*query:\s*(?P<query>.*?)\s*-->\s*$`.
- Closing line: `^[ \t]{0,3}<!--\s*/query(?:\s+(?P<hash>[0-9a-f]{8}))?\s*-->\s*$`.
- A block runs from an opening line to the next closing line. An opening line with no
  closing line before the next opening line (or the end of the file) is a new, empty
  block: the server inserts the results and a closing line directly after it, and
  whatever followed stays below.
- Both are ignored inside fenced code blocks (```` ``` ```` or `~~~` fences).
- The hash is the first 8 hex digits of the SHA-256 of the result lines joined with
  `\n` (no trailing newline). An empty result region hashes like any other.

### Entries

- Entry heading level `E` = level of the nearest heading above the opening line + 1, or 2
  if there is none; capped at 6.
- Journal entry: `{E #s} [{YYYY-MM-DD} {Mon..Sun} · {heading path joined with " › "}]({link})`;
  page entry: `{E #s} [{page title} › {heading path}]({link})`.
- `{link}` = the source note's path relative to the page's folder, then `#` and the
  GitHub slug of the section's own heading. Slug: flatten `[text](url)` to `text`,
  lowercase, trim, delete the characters ``[]!"#$%&'()*+,./:;<=>?@\^`{|}~``, then replace
  each whitespace character with `-` (no second trim: `Budget !` is `budget-`). A repeated
  slug in the same note gets `-1`, `-2`, … in document order (GitHub's rule), counting
  every heading outside fenced code and frontmatter, those inside query blocks included.
  markdown-ts-mode slugs the raw heading text, so gco-pkm teaches it the flattened form
  for headings that contain links.
- `show:titles` renders a list instead: `- [{same title}]({link})`.
- Overflow line: `*{n} more entries not shown.*`; empty: `*No matching entries.*`;
  syntax error: `*Query error: {message}*`.

### Python engine: `pkm_bridge/queries/` (stdlib only, so Emacs can run it)

- `query_block_ranges(text) -> list[tuple[int, int]]`: 0-based, inclusive line ranges,
  opening line through closing line (or just the opening line when unclosed).
- `strip_machine_blocks(text) -> str`: query blocks and `<!-- backlinks -->` …
  `<!-- /backlinks -->` blocks replaced by empty lines, so line numbers stay valid.
- `Corpus(root)`: the notes under an org root, parsed once and cached by mtime and size.
  `Corpus.pages_with_queries() -> list[str]` gives root-relative paths of notes with
  query blocks.
- `refresh_text(text, rel_path, corpus, today) -> RefreshResult`: pure. `RefreshResult`
  has `text` (the refreshed note), `changed`, and `blocks`, each with `query`, `entries`
  (matches before the limit), `error`, `changed` and `hand_edited`.

### CLI

`python -m pkm_bridge.queries` with the repository root on `PYTHONPATH`:

- `refresh-text --root DIR --path REL [--json]`: reads a note on stdin and writes the
  refreshed note to stdout. With `--json`, writes
  `{"text": …, "changed": bool, "blocks": [{"query", "entries", "error", "changed", "hand_edited"}]}`.
- `preview --root DIR --path REL QUERY`: prints the result lines QUERY would produce in
  REL.
- `refresh --root DIR [REL …]`: rewrites the given notes in place (all notes with query
  blocks when none are given) and prints a summary. For local use; the server has its own
  refresher.

### HTTP

- `POST /api/queries/refresh`, body `{"path": "org:pages/x.md"}` (omit `path` for every
  page). Returns `{"pages": [{"path", "status": "saved"|"merged"|"unchanged", "blocks": […]}]}`.
  Runs even when automatic writes are off.
- `POST /api/queries/preview`, body `{"query": …, "path": "org:pages/x.md"}`. Returns
  `{"markdown": …, "entries": n, "error": null|str}`.
