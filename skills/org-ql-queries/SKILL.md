---
name: org-ql-queries
description: Org-QL Queries Skill
---

# Org-QL Queries Skill

This skill provides guidance on constructing and executing org-ql queries via Emacs batch mode.

## What is Org-QL?

org-ql is a powerful query language for org-mode files, similar to SQL but designed for org's hierarchical structure. It can search across multiple files and supports complex predicates.

## Running Org-QL Queries

### Basic Pattern

Use Emacs batch mode to run org-ql queries:

```bash
emacs --batch --eval "
(progn
  (require 'org-ql)
  (let ((results (org-ql-query
                   :select 'WHAT-TO-SELECT
                   :from 'WHERE-TO-SEARCH
                   :where 'QUERY-PREDICATE)))
    (princ (json-encode results))))"
```

### Important Setup

Make sure org-ql is in the load path:

```bash
emacs --batch --eval "
(progn
  (add-to-list 'load-path \"~/.config/emacs/elpa/org-ql-VERSION/\")
  (require 'org-ql)
  ...)"
```

Or if using the user's setup:

```bash
emacs --batch --eval "
(progn
  (load-file \"~/.config/emacs/init.el\")  ; Loads all packages
  (require 'org-ql)
  ...)"
```

## Query Components

### :from - Where to Search

```elisp
; Search in specific file
:from '("$ORG_DIR/journal.org")

; Search in directory (all .org files)
:from '("$ORG_DIR")

; Search multiple files
:from '("$ORG_DIR/journal.org" "$ORG_DIR/projects.org")
```

### :select - What to Return

```elisp
; Just the heading text
:select '(org-get-heading t t t t)

; Heading plus some properties
:select '(list (org-get-heading t t t t)
               (org-entry-get nil "SCHEDULED")
               (org-entry-get nil "DEADLINE"))

; More complex selection
:select '(list :heading (org-get-heading t t t t)
               :tags (org-get-tags)
               :todo (org-get-todo-state))
```

### :where - Query Predicates

This is the main query part - see sections below.

## Common Query Predicates

### TODO States

```elisp
; All TODO items
:where '(todo)

; Specific states
:where '(todo "TODO")
:where '(todo "TODO" "NEXT")
:where '(done)  ; All DONE states
```

### Tags

```elisp
; Has specific tag
:where '(tags "emacs")

; Has multiple tags (OR)
:where '(tags "emacs" "org-mode")

; Has multiple tags (AND) - use 'and'
:where '(and (tags "emacs") (tags "org-mode"))

; Doesn't have tag
:where '(not (tags "archived"))
```

### Properties

```elisp
; Has property with value
:where '(property "project" "gco-pkm")

; Has property (any value)
:where '(property "priority")

; Property comparison
:where '(property "priority" "high")
```

### Dates and Timestamps

```elisp
; Has any timestamp
:where '(ts)

; Timestamp in range
:where '(ts :from -7 :to today)

; Scheduled items
:where '(scheduled)

; Scheduled in next 7 days
:where '(scheduled :to 7)

; Deadline items
:where '(deadline)

; Overdue items
:where '(deadline :to today)

; Closed in date range
:where '(closed :from -30 :to today)
```

### Content Search

```elisp
; Contains text (case-insensitive)
:where '(regexp "sailing")

; Case-sensitive
:where '(regexp "Sailing")

; Multiple terms (OR)
:where '(or (regexp "sailing") (regexp "boat"))
```

### Heading Level

```elisp
; Specific level (1-based)
:where '(level 3)  ; Day entries in journal

; Level range
:where '(and (level >= 2) (level <= 3))
```

### File Path

```elisp
; In specific file
:where '(file-name-match "journal.org")

; In directory
:where '(file-name-match "projects/")

; Not in file
:where '(not (file-name-match "archive"))
```

## Combining Predicates

### AND - All must match

```elisp
:where '(and (todo "TODO")
             (tags "emacs")
             (scheduled :to 7))
```

### OR - Any can match

```elisp
:where '(or (tags "urgent")
            (deadline :to today)
            (property "priority" "high"))
```

### NOT - Must not match

```elisp
:where '(and (todo)
             (not (tags "someday")))
```

### Complex Combinations

```elisp
:where '(and (todo "TODO" "NEXT")
             (or (tags "project")
                 (property "project"))
             (not (scheduled :from 30)))
```

## Complete Working Examples

### Example 1: Find All TODOs

```bash
emacs --batch --eval "
(progn
  (require 'org-ql)
  (let ((results (org-ql-query
                   :select '(org-get-heading t t t t)
                   :from '(\"$ORG_DIR\")
                   :where '(todo))))
    (dolist (item results)
      (princ item)
      (princ \"\n\"))))"
```

### Example 2: TODOs Tagged 'emacs'

```bash
emacs --batch --eval "
(progn
  (require 'org-ql)
  (let ((results (org-ql-query
                   :select '(org-get-heading t t t t)
                   :from '(\"$ORG_DIR\")
                   :where '(and (todo) (tags \"emacs\")))))
    (princ (json-encode results))))"
```

### Example 3: Recent Journal Entries

```bash
emacs --batch --eval "
(progn
  (require 'org-ql)
  (let ((results (org-ql-query
                   :select '(org-get-heading t t t t)
                   :from '(\"$ORG_DIR/journal.org\")
                   :where '(and (level 3)
                                (ts :from -7 :to today)))))
    (princ (json-encode results))))"
```

### Example 4: Active Projects

```bash
emacs --batch --eval "
(progn
  (require 'org-ql)
  (let ((results (org-ql-query
                   :select '(list :heading (org-get-heading t t t t)
                                  :tags (org-get-tags))
                   :from '(\"$ORG_DIR\")
                   :where '(and (tags \"project\")
                                (todo \"TODO\" \"NEXT\")))))
    (princ (json-encode results))))"
```

### Example 5: Overdue Tasks

```bash
emacs --batch --eval "
(progn
  (require 'org-ql)
  (let ((results (org-ql-query
                   :select '(list :heading (org-get-heading t t t t)
                                  :deadline (org-entry-get nil \"DEADLINE\"))
                   :from '(\"$ORG_DIR\")
                   :where '(and (todo)
                                (deadline :to today)))))
    (princ (json-encode results))))"
```

## Sorting Results

Add `:sort` parameter:

```elisp
:sort 'date          ; By timestamp
:sort 'deadline      ; By deadline
:sort 'priority      ; By priority
:sort '(date deadline)  ; Multiple sorts
```

Example:

```bash
emacs --batch --eval "
(progn
  (require 'org-ql)
  (let ((results (org-ql-query
                   :select '(org-get-heading t t t t)
                   :from '(\"$ORG_DIR\")
                   :where '(todo)
                   :sort 'deadline)))
    (princ (json-encode results))))"
```

## Tips for User's PKM

Based on the journal structure:

### Find Music Notes
```elisp
:where '(and (regexp "#music")
             (ts :from -90))  ; Last 3 months
```

### Find Sailing Trip References
```elisp
:where '(or (regexp "#sailing")
            (regexp "sailing trip"))
```

### Find Health-Related Entries
```elisp
:where '(or (tags "health")
            (regexp "#health")
            (regexp "#covid"))
```

### Find Work on Emacs PKM
```elisp
:where '(and (or (regexp "emacs.*pkm")
                 (regexp "gco-pkm")
                 (tags "gco-pkm"))
             (ts :from -30))
```

## Troubleshooting

### Error: org-ql not found
```bash
# Make sure org-ql is loaded in init.el or specify path:
emacs --batch --eval "(add-to-list 'load-path \"PATH/TO/ORG-QL\")" ...
```

### Error: Invalid query
```bash
# Check parentheses are balanced
# Predicates must be quoted: '(todo) not (todo)
# File paths need proper escaping in shell
```

### Empty results
```bash
# Verify :from path is correct
# Try simpler query first: '(todo)
# Check if files actually contain matching entries
```

## Best Practices

1. **Start simple**: Begin with `'(todo)` and add complexity
2. **Test incrementally**: Add one predicate at a time
3. **Use JSON output**: Easier to parse programmatically
4. **Quote everything**: Predicates, file paths, etc.
5. **Check file paths**: Use $ORG_DIR variable for portability

## Advanced: Programmatic Use

Save query results for further processing:

```bash
# Get JSON output
RESULTS=$(emacs --batch --eval "..." 2>&1 | tail -1)

# Process with jq
echo "$RESULTS" | jq '.[] | select(.priority == "A")'

# Count results
echo "$RESULTS" | jq 'length'
```

## Integration with Other Tools

### Combine with ripgrep
```bash
# org-ql finds TODOs, ripgrep shows context
emacs --batch --eval "..." | while read heading; do
  rg "$heading" --context=2 "$ORG_DIR"
done
```

### Export to markdown
```bash
# Query results -> markdown list
emacs --batch --eval "..." | \
  jq -r '.[] | "- [ ] \(.)"' > tasks.md
```
