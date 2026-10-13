"""The query language: search-engine style terms plus a few options.

Egypt                a heading word, or a #Egypt tag in the text
text:Egypt           the word anywhere, plain mentions included
heading:Egypt        headings only
"Long Now"           a phrase
a b, a AND b         both
a OR b               either; binds tighter than AND
-a, NOT a            not
( )                  grouping
is:todo|done         task items instead of sections
in:journals|pages|all, after:/before: (YYYY-MM-DD, or 30d for 30 days ago), last:90d,
limit:N, sort:newest|oldest, show:full|titles|list
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Union

DEFAULT_LIMIT = 50

# Generic journal headings: a bare word from this list never matches a heading.
HEADING_STOP_WORDS = frozenset(
    {"today", "tonight", "tomorrow", "yesterday", "misc", "notes", "todo", "tasks"}
)

_TOKEN_RE = re.compile(r'\s*(?:(?P<paren>[()])|(?P<word>-?(?:[^\s()"]*"[^"]*"?|[^\s()"]+)))')
_FIELD_RE = re.compile(r"^(?P<name>[a-z]+):(?P<value>.+)$")
_AGO_RE = re.compile(r"^(\d+)([dwmy])$")
_AGO_UNIT_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}
_TERM_FIELDS = ("text", "heading")
_OPTION_FIELDS = ("in", "after", "before", "last", "limit", "show", "sort", "is")
_OPERATORS = ("OR", "AND")


class QuerySyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Term:
    text: str
    scope: str  # "topic" (bare word), "text" or "heading"
    pattern: re.Pattern = field(compare=False, repr=False)

    @property
    def is_word(self) -> bool:
        return " " not in self.text


@dataclass(frozen=True)
class Not:
    arg: "Node"


@dataclass(frozen=True)
class And:
    args: tuple["Node", ...]


@dataclass(frozen=True)
class Or:
    args: tuple["Node", ...]


Node = Union[Term, Not, And, Or]


@dataclass
class Query:
    expr: Node | None  # None matches every section in scope
    scope: str = "journals"
    limit: int = DEFAULT_LIMIT
    show: str = "full"
    sort: str = "newest"
    task: str | None = None  # "todo" or "done": task items instead of sections
    after: date | timedelta | None = None  # a timedelta counts back from today
    before: date | timedelta | None = None
    last: timedelta | None = None

    def date_range(self, today: date) -> tuple[date | None, date | None]:
        """Inclusive first and exclusive last date, either possibly open."""
        firsts = [_resolve(self.after, today), _resolve(self.last, today)]
        first = max((d for d in firsts if d), default=None)
        return first, _resolve(self.before, today)

    @property
    def dated(self) -> bool:
        return any(x is not None for x in (self.after, self.before, self.last))


def _resolve(when: date | timedelta | None, today: date) -> date | None:
    return today - when if isinstance(when, timedelta) else when


def word_pattern(text: str) -> re.Pattern:
    """Case-insensitive whole-word match for a word or a phrase."""
    body = r"\s+".join(re.escape(w) for w in text.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.I)


def parse_query(source: str) -> Query:
    tokens = [m.group("paren") or m.group("word") for m in _TOKEN_RE.finditer(source)]
    query = Query(expr=None)
    terms: list[str] = []
    for tok in tokens:
        m = _FIELD_RE.match(tok)
        if m and m.group("name") in _OPTION_FIELDS:
            _set_option(query, m.group("name"), m.group("value").strip('"'))
        else:
            terms.append(tok)
    if not terms and not query.dated and not query.task and query.scope == "journals":
        raise QuerySyntaxError("empty query: give a word, or an option such as is:todo or last:7d")
    if query.show == "list" and not query.task:
        raise QuerySyntaxError("show:list works with is:todo or is:done")
    if terms:
        parser = _Parser(terms)
        query.expr = parser.sequence()
        if parser.pos < len(terms):
            raise QuerySyntaxError("unbalanced ')'")
    return query


def _set_option(query: Query, name: str, value: str) -> None:
    if name == "in":
        if value not in ("journals", "pages", "all"):
            raise QuerySyntaxError(f"in: takes journals, pages or all, not {value!r}")
        query.scope = value
    elif name == "show":
        if value not in ("full", "titles", "list"):
            raise QuerySyntaxError(f"show: takes full, titles or list, not {value!r}")
        query.show = value
    elif name == "sort":
        if value not in ("newest", "oldest"):
            raise QuerySyntaxError(f"sort: takes newest or oldest, not {value!r}")
        query.sort = value
    elif name == "is":
        if value not in ("todo", "done"):
            raise QuerySyntaxError(f"is: takes todo or done, not {value!r}")
        query.task = value
    elif name == "limit":
        if not value.isdigit() or int(value) < 1:
            raise QuerySyntaxError(f"limit: takes a positive number, not {value!r}")
        query.limit = int(value)
    elif name == "last":
        if (ago := _ago(value)) is None:
            raise QuerySyntaxError(f"last: takes a count and d, w, m or y, like 90d, not {value!r}")
        query.last = ago
    else:  # after, before
        ago = _ago(value)
        try:
            setattr(query, name, ago if ago is not None else date.fromisoformat(value))
        except ValueError:
            raise QuerySyntaxError(
                f"{name}: takes a date like 2026-01-31 or a time ago like 30d, not {value!r}"
            ) from None


def _ago(value: str) -> timedelta | None:
    """`90d`, `2w`, `3m` or `1y` as a time span, or None."""
    m = _AGO_RE.match(value)
    return timedelta(days=int(m.group(1)) * _AGO_UNIT_DAYS[m.group(2)]) if m else None


class _Parser:
    """sequence := disjunction ([AND] disjunction)*;  disjunction := unary (OR unary)*;
    unary := ('-' | NOT) unary | '(' sequence ')' | term."""

    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def sequence(self) -> Node:
        args = []
        while self.peek() not in (None, ")"):
            if self.peek() == "AND":
                self.take()
                if not args or self.peek() in (None, ")", *_OPERATORS):
                    raise QuerySyntaxError("AND needs a term on each side")
                continue
            args.append(self.disjunction())
        if not args:
            raise QuerySyntaxError("empty group")
        return args[0] if len(args) == 1 else And(tuple(args))

    def disjunction(self) -> Node:
        args = [self.unary()]
        while self.peek() == "OR":
            self.take()
            if self.peek() in (None, ")", *_OPERATORS):
                raise QuerySyntaxError("OR needs a term on each side")
            args.append(self.unary())
        return args[0] if len(args) == 1 else Or(tuple(args))

    def unary(self) -> Node:
        tok = self.take()
        if tok in _OPERATORS:
            raise QuerySyntaxError(f"{tok} needs a term on each side")
        if tok == "NOT":
            if self.peek() in (None, ")", *_OPERATORS):
                raise QuerySyntaxError("NOT needs a term after it")
            return Not(self.unary())
        if tok == ")":
            raise QuerySyntaxError("unbalanced ')'")
        if tok == "(":
            node = self.sequence()
            if self.peek() != ")":
                raise QuerySyntaxError("missing ')'")
            self.take()
            return node
        if tok.startswith("-") and len(tok) > 1:
            self.tokens[self.pos - 1] = tok[1:]
            self.pos -= 1
            return Not(self.unary())
        if tok == "-":
            if self.peek() != "(":
                raise QuerySyntaxError("'-' needs a term after it")
            return Not(self.unary())
        return _term(tok)


def _term(tok: str) -> Term:
    scope = "topic"
    m = _FIELD_RE.match(tok)
    if m and m.group("name") in _TERM_FIELDS:
        scope, tok = m.group("name"), m.group("value")
    text = " ".join(tok.strip('"').split()).lstrip("#")
    if not text:
        raise QuerySyntaxError("empty term")
    return Term(text, scope, word_pattern(text))
