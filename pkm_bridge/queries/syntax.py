"""The query language: search-engine style terms plus a few options.

Egypt                a heading word, or a #Egypt tag in the text
text:Egypt           the word anywhere, plain mentions included
heading:Egypt        headings only
"Long Now"           a phrase
a b, a OR b, -a, ( )  and, or (binds tighter), not, grouping
in:journals|pages|all, after:/before:YYYY-MM-DD, last:90d, limit:N, show:full|titles
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
_LAST_RE = re.compile(r"^(\d+)([dwmy])$")
_LAST_UNIT_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}
_TERM_FIELDS = ("text", "heading")
_OPTION_FIELDS = ("in", "after", "before", "last", "limit", "show")


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
    after: date | None = None
    before: date | None = None
    last: timedelta | None = None

    def date_range(self, today: date) -> tuple[date | None, date | None]:
        """Inclusive first and exclusive last journal date, either possibly open."""
        first = self.after
        if self.last is not None:
            since = today - self.last
            first = max(first, since) if first else since
        return first, self.before

    @property
    def dated(self) -> bool:
        return any(x is not None for x in (self.after, self.before, self.last))


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
    if not terms and not query.dated and query.scope == "journals":
        raise QuerySyntaxError("empty query")
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
        if value not in ("full", "titles"):
            raise QuerySyntaxError(f"show: takes full or titles, not {value!r}")
        query.show = value
    elif name == "limit":
        if not value.isdigit() or int(value) < 1:
            raise QuerySyntaxError(f"limit: takes a positive number, not {value!r}")
        query.limit = int(value)
    elif name == "last":
        m = _LAST_RE.match(value)
        if not m:
            raise QuerySyntaxError(f"last: takes a count and d, w, m or y, like 90d, not {value!r}")
        query.last = timedelta(days=int(m.group(1)) * _LAST_UNIT_DAYS[m.group(2)])
    else:  # after, before
        try:
            setattr(query, name, date.fromisoformat(value))
        except ValueError:
            raise QuerySyntaxError(f"{name}: takes a date like 2026-01-31, not {value!r}") from None


class _Parser:
    """sequence := disjunction*;  disjunction := unary (OR unary)*;
    unary := '-' unary | '(' sequence ')' | term."""

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
            args.append(self.disjunction())
        if not args:
            raise QuerySyntaxError("empty group")
        return args[0] if len(args) == 1 else And(tuple(args))

    def disjunction(self) -> Node:
        args = [self.unary()]
        while self.peek() == "OR":
            self.take()
            if self.peek() in (None, ")", "OR"):
                raise QuerySyntaxError("OR needs a term on each side")
            args.append(self.unary())
        return args[0] if len(args) == 1 else Or(tuple(args))

    def unary(self) -> Node:
        tok = self.take()
        if tok == "OR":
            raise QuerySyntaxError("OR needs a term on each side")
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
