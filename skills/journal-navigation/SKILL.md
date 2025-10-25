---
name: journal-navigation
description: Journal Navigation Skill
---

# Journal Navigation Skill

This skill provides guidance on working with the hierarchical journal structure used in the user's PKM system.

## Journal Structure

The journal is stored in `journal.org` with this hierarchy:

```org
* 2025                          (Level 1: Year)
** 2025-10 October             (Level 2: Month)
*** <2025-10-24 Thu>           (Level 3: Day with active timestamp)
:PROPERTIES:
:ID: UUID-HERE
:END:

- Daily notes here...
- Can include #hashtags
- [[wiki:links]] and [[file:paths]]

**** TODO Inline task items
*** <2025-10-25 Fri>
...
```

## Finding Today's Entry

### Quick Search
```bash
# Get today's date in org format
TODAY=$(date +"%Y-%m-%d")

# Find today's entry
rg "<$TODAY" journal.org
```

### Get Full Day's Content
```bash
# Get everything under today's heading
# This is approximate - better to use Emacs/org tools
TODAY=$(date +"%Y-%m-%d")
rg -A 50 "^\\*\\*\\* <$TODAY" journal.org
```

## Finding Entries by Date

### Specific Date
```bash
rg "<2025-10-24" journal.org
```

### Date Range
```bash
# All October 2025 entries
rg "^\\*\\*\\* <2025-10-" journal.org

# Specific week (Oct 1-7)
rg "^\\*\\*\\* <2025-10-0[1-7]" journal.org
```

### This Week/Month/Year
```bash
# This month
MONTH=$(date +"%Y-%m")
rg "^\\*\\*\\* <$MONTH" journal.org

# This year
YEAR=$(date +"%Y")
rg "^\\*\\*\\* <$YEAR" journal.org
```

## Adding Content to Journal

**IMPORTANT**: The user has a custom Emacs function `gco-pkm-journal-today` that properly handles journal structure using org-ml. Prefer using Emacs batch mode over manual text manipulation. All journal additions **must** respect the date hierarchy, and be added under the proper date.

### Using Emacs Batch Mode (Recommended)

```bash
emacs --batch \
  --eval "(progn
    (add-to-list 'load-path \"~/.config/emacs/lisp\")
    (require 'gco-pkm)
    (find-file \"$ORG_DIR/journal.org\")
    (gco-pkm-journal-today)
    (insert \"\n- New entry here\n\")
    (save-buffer)
    (message \"Added to journal\"))"
```

This approach:
- Handles creating today's entry if it doesn't exist
- Creates parent year/month structure if needed
- Uses reliable AST-based manipulation (org-ml)
- Properly formats timestamps and property drawers

### Quick Append (Simple Cases Only)

Do NOT attempt to just append to the org journal file. Always use Emacs.

## Extracting Information

### Count Entries per Month
```bash
rg -c "^\\*\\*\\* <" journal.org | \
  awk -F'<' '{print substr($2,1,7)}' | \
  sort | uniq -c
```

### List All Days with Entries
```bash
rg -o "^\\*\\*\\* <[0-9]{4}-[0-9]{2}-[0-9]{2}" journal.org | \
  sed 's/^\\*\\*\\* <//' | \
  sort
```

### Get Summary of Recent Entries
```bash
# Last 7 days
WEEK_AGO=$(date -v-7d +"%Y-%m-%d")  # macOS
# WEEK_AGO=$(date -d "7 days ago" +"%Y-%m-%d")  # Linux

rg "^\\*\\*\\* <" journal.org | \
  awk -v start="$WEEK_AGO" '$0 > start' | \
  head -n 10
```

## Working with Monthly Structure

### Find Specific Month
```bash
rg "^\\*\\* 2025-10 October" journal.org
```

### List All Months
```bash
rg "^\\*\\* [0-9]{4}-[0-9]{2}" journal.org
```

### Count Days in Month
```bash
# Days in October 2025
rg -c "^\\*\\*\\* <2025-10-" journal.org
```

## Property Drawers

Each day entry has a property drawer with a unique ID:

```org
*** <2025-10-24 Thu>
:PROPERTIES:
:ID: ABC123-DEF456-...
:END:
```

### Find Entry by ID
```bash
rg ":ID:.*ABC123" journal.org
```

### Extract All IDs
```bash
rg -o ":ID:.*" journal.org | sed 's/:ID: *//'
```

## TODO Items in Journal

TODOs can be:
1. Inline under daily entries
2. In separate "Tasks" section at end of file

### Find TODOs in Date Range
```bash
# TODOs from October 2025
rg "^\\*\\*\\*\\* TODO" journal.org | \
  awk '/<2025-10-/,/<2025-11-01/'
```

### Find Inline TODOs for Today
```bash
TODAY=$(date +"%Y-%m-%d")
rg -A 20 "^\\*\\*\\* <$TODAY" journal.org | \
  rg "^\\*\\*\\*\\* TODO"
```

## Tips for User's Journal

Based on the structure observed:

1. **Active timestamps**: Days use `<YYYY-MM-DD Day>` format
2. **Four-star TODOs**: Inline tasks are `**** TODO`
3. **Property drawers**: Every day has `:ID:` and `:END:`
4. **Bullet lists**: Notes use `-` for bullets
5. **Hashtags everywhere**: Liberal use of `#tags`

### Common Patterns

**Morning journal entry**:
- Has timestamp
- Often includes tasks for the day
- May reference previous day

**Evening notes**:
- Appended to day's entry
- Often tagged #diary
- May have TODO items created

**Task tracking**:
- Some TODOs inline in journal
- Some in separate Tasks section
- Uses scheduling: `SCHEDULED: <date>`

## Error Handling

### If Today's Entry Doesn't Exist

Don't try to create it manually - use Emacs batch mode with `gco-pkm-journal-today`.

### If Month Structure is Missing

The `gco-pkm-journal-today` function will create it properly:
- Creates year heading if needed
- Creates month heading if needed
- Creates day heading with proper timestamp
- Adds property drawer with ID

## Best Practices

1. **Always use Emacs batch mode for adding content**
2. **Use ripgrep for searching/reading only**
3. **Don't manually edit structure** (dates, properties)
4. **Respect the hierarchy** (Year > Month > Day)
5. **Include hashtags** when adding content
6. **Generate proper UUIDs** for new entries (Emacs does this)

## Example Workflows

### Daily Review
```bash
# Show last 3 days of entries
for i in 0 1 2; do
  DAY=$(date -v-${i}d +"%Y-%m-%d")  # macOS
  echo "=== $DAY ==="
  rg -A 10 "^\\*\\*\\* <$DAY" journal.org
done
```

### Weekly Summary
```bash
# Count hashtags used this week
WEEK_AGO=$(date -v-7d +"%Y-%m-%d")
rg "^\\*\\*\\* <" journal.org | \
  awk -v start="$WEEK_AGO" '$0 > start' | \
  rg -o "#[a-z0-9_-]+" | \
  sort | uniq -c | sort -rn
```

### Find Pattern in Time Range
```bash
# Mentions of "sailing" in July-August
rg -i "sailing" journal.org | \
  grep "<2025-0[78]-"
```
