# Search Patterns Skill

This skill provides guidance on effective searching in org-mode files using ripgrep.

## Basic Text Search

Use `rg` (ripgrep) for fast searching:

```bash
# Simple case-insensitive search
rg -i "term" .

# Search with context (2 lines before and after)
rg -i "term" --context=2 .

# Search in specific file
rg -i "term" journal.org
```

## Hashtag Search

The user's PKM system uses inline hashtags like `#emacs`, `#music`, `#health`.

### Simple Hashtag Search
```bash
# Search for #emacs (case-insensitive)
rg -i "#emacs" .
```

### Precise Hashtag Search (Word Boundaries)

To avoid matching `#emacs-lisp` when searching for `#emacs`, or `super#emacs`:

```bash
# Use PCRE regex with word boundaries
rg -P "(?<![[:alnum:]])#emacs(?![[:alnum:]])" .
```

Pattern explanation:
- `-P`: Enable PCRE regex
- `(?<![[:alnum:]])`: Negative lookbehind - no alphanumeric before
- `#emacs`: The hashtag
- `(?![[:alnum:]])`: Negative lookahead - no alphanumeric after

### Multiple Hashtags

```bash
# Find entries with #music AND #concert
rg -i "#music" . | rg -i "#concert"

# Find entries with #music OR #concert
rg -i "#music|#concert" .
```

## Date-Based Search

The journal uses active timestamps: `<2025-10-24 Thu>`

### Search Specific Date
```bash
rg "<2025-10-24" journal.org
```

### Search Date Range
```bash
# All entries in October 2025
rg "<2025-10-" journal.org

# All entries in July-August 2025
rg "<2025-(07|08)-" journal.org
```

### Recent Entries
Use `find` to get recently modified files, then search:

```bash
# Files modified in last 7 days
find . -name "*.org" -mtime -7 -exec rg -i "term" {} +
```

## Content Search Patterns

### TODO Items
```bash
# All TODO items
rg "^\*+ TODO" .

# TODO items with priority
rg "^\*+ TODO \[#[ABC]\]" .

# DONE items
rg "^\*+ DONE" .
```

### Headings
```bash
# All top-level headings
rg "^\* [^*]" .

# All day entries (3 asterisks + timestamp)
rg "^\*\*\* <[0-9]{4}-[0-9]{2}-[0-9]{2}" journal.org
```

### Links
```bash
# Wiki links
rg "\[\[wiki:" .

# File links
rg "\[\[file:" .

# All org links
rg "\[\[.*\]\]" .
```

### Property Drawers
```bash
# Find entries with specific property
rg ":PROPERTY_NAME:" .

# Find all IDs
rg ":ID:" .
```

## Output Formatting

```bash
# Count matches per file
rg -c "term" .

# Show only filenames (no content)
rg -l "term" .

# Show only matching text (no line numbers/filenames)
rg -o "term" .

# JSON output (for programmatic use)
rg --json "term" .
```

## Performance Tips

1. **Be specific**: Use `-g '*.org'` to search only org files
2. **Use fixed strings**: `-F` when you don't need regex
3. **Limit depth**: Use `find -maxdepth` for shallow searches
4. **Context is costly**: Only add `--context` when needed

## Common Query Patterns

### "What did I write about X?"
```bash
rg -i "X" --context=2 .
```

### "When did I last mention X?"
```bash
rg -i "X" . | tail -n 5
```

### "How many times did I mention X?"
```bash
rg -c -i "X" . | awk -F: '{sum+=$2} END {print sum}'
```

### "Find all entries with person's name"
```bash
# Names are usually capitalized
rg "\bPersonName\b" .
```

## Combining with Other Tools

### Search then sort by date
```bash
rg "<2025-" journal.org | sort
```

### Search then count unique dates
```bash
rg -o "<[0-9]{4}-[0-9]{2}-[0-9]{2}" journal.org | sort -u | wc -l
```

### Search in git history
```bash
git log -S "search term" --oneline -- *.org
```

## Tips for User's PKM System

Based on the journal structure you saw:

1. **Journal is hierarchical**: Year > Month > Day
2. **Days have timestamps**: `<YYYY-MM-DD Day>`
3. **Common hashtags**: #emacs, #music, #sailing, #health, #work, #diary
4. **TODO items**: Inline in daily entries or in Tasks section
5. **Property drawers**: Each day and task has `:ID:` property

When searching, consider:
- Journal entries are under daily timestamps
- Tasks might be separate or inline
- Hashtags are used liberally for categorization
- Links use wiki: or file: prefixes
