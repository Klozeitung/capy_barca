"""
Formula Engine
==============

A pure-Python, zero-eval formula evaluator for database properties.

Architecture
------------
1. Lexer      – converts an expression string into a flat token stream.
2. Parser     – Pratt (top-down operator precedence) recursive-descent
                parser that transforms the token stream into an AST.
3. Evaluator  – walks the AST, resolves prop() references via a caller-
                supplied context dict, and returns a scalar result.

Supported syntax
----------------
Literals        : number (int / float), string (single or double quoted),
                  true, false, null
Arithmetic      : +  -  *  /  %  ^  (power, right-associative)
Comparison      : ==  !=  <  <=  >  >=
Logical         : and  or  not  (keyword operators)
Unary           : -  not
Grouping        : (expr)
Property access : prop("Column Name")  or  prop('Column Name')
                  Stored formulas name properties by id, prop("<schema id>"),
                  so a rename does not break them; the API and the editor
                  show names (see ``map_prop_references``). The evaluation
                  context therefore carries every value under the property's
                  id and its name.
Functions       : abs(x)  round(x[, digits])  ceil(x)  floor(x)
                  sum(a, …)  min(a, …)  max(a, …)  avg(a, …)  divide(a, b)
                  len(s)  concat(s, …)  empty(x)  format(x)  toNumber(x)
                  contains(text, sub)  equal(a, b)  divide(a, b)
                  style(x, hint, …)  unstyle(x)
                  at(list, index)
                  if(cond, then, else)
                  ifs(cond1, val1, cond2, val2, …[, default])
                  and(a, b, …)  or(a, b, …)  not(a)
                  now()  today()
                  dateAdd(date, n, unit)  dateSubtract(date, n, unit)
                  dateBetween(date1, date2, unit)
                  formatDate(date, format)  parseDate(text)
                  dateStart(date)  dateEnd(date)
                  date(d)  month(d)  year(d)  day(d)
                  hour(d)  minute(d)  week(d)
String concat   : "hello" + " world"  (implicit when either side is string)

Empty / null handling
---------------------
An empty source value — ``None`` or the empty string ``""`` — participates in
arithmetic as ``0`` *only when combined with a concrete value*. When every
operand of an arithmetic operation is empty, the result is relayed as empty
(``None``) rather than fabricated as ``0``; because empty sub-results
propagate, a fully empty expression such as ``prop("A") + prop("B")`` yields
empty, while ``prop("A") + prop("B")`` with one value present yields that
value, and ``prop("A") + 5`` yields ``5`` (the literal counts as concrete).
The same all-empty rule applies to unary minus and ``abs``/``round``/``ceil``/
``floor``. Ordered comparisons (``<`` …) instead coerce empty to ``0`` so they
always return a boolean, and ``dateAdd``/``dateSubtract`` treat an empty amount
as ``0`` (no shift). The variadic aggregates ``sum``/``min``/``max``/``avg``
skip empty values entirely, and ``toNumber("")`` returns empty (``None``) — the
Notion idiom for "empty number".

Date units (dateAdd / dateSubtract / dateBetween)
-------------------------------------------------
  "years"  "quarters"  "months"  "weeks"  "days"  "hours"  "minutes"
  Singular forms ("year", "day", …) are also accepted.
  dateBetween counts completed units only and is the inverse of dateAdd:
  dateAdd(d, dateBetween(e, d, u), u) never lies after e (for e >= d).

formatDate tokens (Moment.js-style)
------------------------------------
  YYYY YY  MMMM MMM MM M  DDDD DDD DD D
  HH H  hh h  mm m  ss s  A a

Operator precedence (high → low)
---------------------------------
  ^  >  unary  >  * / %  >  + -  >  comparisons  >  not  >  and  >  or

Functions are registered with their handler, category and signature in one
registry (``_function``); evaluation, validation and the editor's syntax
reference all read it.

Public API
----------
  evaluate(expression, context)   -> FormulaResult
  extract_prop_names(expression)  -> list[str]
  map_prop_references(expression, mapping) -> str
  uses_volatile_functions(expression) -> bool
  validate_syntax(expression)     -> None  (raises FormulaError on error)
  syntax_catalogue()              -> list[SyntaxEntry]

Resource limits
---------------
Formulas are written by authenticated users and run on the server, so an
expression is bounded by its structure before it is allowed to consume
anything. Its length is deliberately not bounded: work grows with nesting,
chaining and exponentiation, all of which are capped below, while a long but
flat expression – an ``ifs()`` with many branches, say – is linear to lex,
parse and evaluate.

  * nesting         – the parser carries a depth counter and raises
                      ``FormulaError`` at ``MAX_PARSE_DEPTH``. Without it,
                      deeply nested parentheses exhaust the interpreter stack
                      and the resulting ``RecursionError`` escapes as a 500
                      instead of appearing as a formula error in the UI.
  * chaining        – ``a + b + c + …`` parses in a loop but produces a tree
                      as deep as the chain is long, which the evaluator then
                      walks recursively. Bounded separately by
                      ``MAX_OPERATOR_CHAIN``.
  * exponentiation  – ``^`` is bounded in both the exponent and the size of
                      the number it would produce. ``9^9^9`` is three
                      characters of input and an unbounded amount of work.

All of them raise ``FormulaError``, so every caller that already handles a bad
formula handles these as well. The three public entry points guarantee that:
whatever happens inside, what leaves them is a ``FormulaError``.
"""

from __future__ import annotations

import calendar as _calendar
import math
import re
from dataclasses import dataclass, field
from datetime import datetime as _Datetime, timedelta as _timedelta, timezone as _tz
from enum import Enum, auto
from typing import Any, Callable, Mapping, Optional

# ─── Public types ─────────────────────────────────────────────────────────────

FormulaValue = Any  # number | str | bool | datetime | None


# ── Resource limits ───────────────────────────────────────────────────────────
# Deliberately generous: a hand-written formula does not come close to any of
# them, while each bounds a way for one expression to occupy the server.
#
# Two separate bounds, because two different shapes of expression end up as
# depth in the AST, and the AST is what the evaluator walks recursively.
#
#   MAX_PARSE_DEPTH    – nesting: parentheses, function arguments, and the
#                        right-hand side of '^'. Each level is one recursive
#                        call in the parser as well.
#   MAX_OPERATOR_CHAIN – chaining: 'a + b + c + …'. The parser handles this in
#                        a loop and never recurses, but every operator still
#                        adds a level to the left spine of the tree, so a long
#                        enough chain exhausts the stack during evaluation.
#
# They add rather than multiply along any one path, so the deepest reachable
# tree is roughly the sum of the two and stays well inside the interpreter's
# own recursion limit. The parser spends about three interpreter frames per
# nesting level and the evaluator about two, so 128 levels plus a full chain
# keep a wide margin below Python's default limit of 1000 frames even after
# the web stack and the recompute cascade have taken their share. That allows
# roughly 120 nested if() calls, which hand-written formulas do not approach.
MAX_PARSE_DEPTH = 128
MAX_OPERATOR_CHAIN = 200

# Bounds for '^'. The exponent cap alone is not enough, because the base can
# itself be the result of a previous power: the digit estimate is what stops a
# chain from compounding.
MAX_POWER_EXPONENT = 1000
MAX_POWER_RESULT_DIGITS = 4096


@dataclass
class FormulaResult:
    result: FormulaValue
    error: Optional[str] = None
    style: list[str] = field(default_factory=list)


@dataclass
class _Styled:
    """
    Internal wrapper carrying display-style hints alongside a formula value.

    Created exclusively by ``style()``. Stripped at the top level of
    ``evaluate()``, which moves the hints into ``FormulaResult.style``.

    All binary operators and type-coercing functions unwrap this
    transparently so that styled values participate in arithmetic and
    comparisons as if they were plain values. ``__bool__`` delegates to the
    underlying value so that ``_Styled`` works correctly inside ``if()``,
    ``and()``, ``or()``, and logical short-circuits.
    """

    value: FormulaValue
    styles: list[str]

    def __bool__(self) -> bool:
        return bool(self.value) if self.value is not None else False


class FormulaError(Exception):
    """Raised for syntax, type, or runtime errors in a formula expression."""


# ─── Token types ──────────────────────────────────────────────────────────────


class TT(Enum):
    NUMBER = auto()
    STRING = auto()
    IDENT = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    CARET = auto()
    EQ = auto()
    NEQ = auto()
    LT = auto()
    LTE = auto()
    GT = auto()
    GTE = auto()
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    TRUE = auto()
    FALSE = auto()
    NULL = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    EOF = auto()


@dataclass
class Token:
    type: TT
    value: Any = None
    pos: int = 0


# ─── Lexer ────────────────────────────────────────────────────────────────────

_KEYWORDS: dict[str, TT] = {
    "true": TT.TRUE,
    "false": TT.FALSE,
    "null": TT.NULL,
    "and": TT.AND,
    "or": TT.OR,
    "not": TT.NOT,
}

_NUMBER_RE = re.compile(r"\d+(\.\d+)?")
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
_ESCAPE_MAP: dict[str, str] = {
    "n": "\n", "t": "\t", "r": "\r",
    "\\": "\\", "'": "'", '"': '"',
}


def _lex(src: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(src)

    while i < n:
        c = src[i]

        # whitespace
        if c in " \t\r\n":
            i += 1
            continue

        # string literal
        if c in ("'", '"'):
            quote = c
            j = i + 1
            buf: list[str] = []
            while j < n and src[j] != quote:
                if src[j] == "\\" and j + 1 < n:
                    buf.append(_ESCAPE_MAP.get(src[j + 1], src[j + 1]))
                    j += 2
                else:
                    buf.append(src[j])
                    j += 1
            if j >= n:
                raise FormulaError(f"Unterminated string starting at position {i}")
            tokens.append(Token(TT.STRING, "".join(buf), i))
            i = j + 1
            continue

        # number
        m = _NUMBER_RE.match(src, i)
        if m:
            raw = m.group()
            tokens.append(Token(TT.NUMBER, float(raw) if "." in raw else int(raw), i))
            i = m.end()
            continue

        # identifier / keyword
        m = _IDENT_RE.match(src, i)
        if m:
            word = m.group()
            tt = _KEYWORDS.get(word.lower(), TT.IDENT)
            tokens.append(Token(tt, word if tt is TT.IDENT else word.lower(), i))
            i = m.end()
            continue

        # two-character operators
        two = src[i : i + 2]
        if two == "==":
            tokens.append(Token(TT.EQ, two, i)); i += 2; continue
        if two == "!=":
            tokens.append(Token(TT.NEQ, two, i)); i += 2; continue
        if two == "<=":
            tokens.append(Token(TT.LTE, two, i)); i += 2; continue
        if two == ">=":
            tokens.append(Token(TT.GTE, two, i)); i += 2; continue

        # single-character operators
        _SINGLE: dict[str, TT] = {
            "+": TT.PLUS, "-": TT.MINUS, "*": TT.STAR, "/": TT.SLASH,
            "%": TT.PERCENT, "^": TT.CARET,
            "<": TT.LT, ">": TT.GT,
            "(": TT.LPAREN, ")": TT.RPAREN, ",": TT.COMMA,
        }
        if c in _SINGLE:
            tokens.append(Token(_SINGLE[c], c, i)); i += 1; continue

        raise FormulaError(f"Unexpected character '{c}' at position {i}")

    tokens.append(Token(TT.EOF, None, n))
    return tokens


# ─── AST nodes ────────────────────────────────────────────────────────────────


@dataclass
class NumberLit:
    value: float | int


@dataclass
class StringLit:
    value: str


@dataclass
class BoolLit:
    value: bool


@dataclass
class NullLit:
    pass


@dataclass
class BinOp:
    op: str
    left: Any
    right: Any


@dataclass
class UnaryOp:
    op: str
    operand: Any


@dataclass
class FuncCall:
    name: str
    args: list[Any]


# ─── Parser ───────────────────────────────────────────────────────────────────

# Pratt precedence table (higher = binds tighter)
_PREC: dict[TT, int] = {
    TT.OR: 10,
    TT.AND: 20,
    TT.EQ: 30, TT.NEQ: 30,
    TT.LT: 40, TT.LTE: 40, TT.GT: 40, TT.GTE: 40,
    TT.PLUS: 50, TT.MINUS: 50,
    TT.STAR: 60, TT.SLASH: 60, TT.PERCENT: 60,
    TT.CARET: 70,  # right-associative: handled by lowering right-side prec
}


class _Parser:
    __slots__ = ("_tokens", "_pos", "_depth")

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0
        self._depth = 0

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        t = self._tokens[self._pos]
        self._pos += 1
        return t

    def _expect(self, tt: TT) -> Token:
        t = self._advance()
        if t.type != tt:
            raise FormulaError(
                f"Expected {tt.name} but got '{t.value}' ({t.type.name}) at position {t.pos}"
            )
        return t

    def _match(self, *types: TT) -> bool:
        return self._peek().type in types

    def _prec(self) -> int:
        return _PREC.get(self._peek().type, 0)

    def _descend(self) -> None:
        """
        Count one level of recursion and refuse to go deeper than the cap.

        Every level of grouping routes through ``_expr`` exactly once, so one
        counter here covers parentheses, function arguments and the
        right-hand side of ``^`` alike. Prefix operators are handled without
        recursion and carry their own bound. Without
        it the interpreter stack runs out first, and a ``RecursionError`` is
        not something the callers of this module are prepared for: it leaves
        as a 500 rather than as a formula error the editor can display.
        """
        self._depth += 1
        if self._depth > MAX_PARSE_DEPTH:
            raise FormulaError(
                f"Expression is nested too deeply, the limit is {MAX_PARSE_DEPTH} levels"
            )

    # ── Grammar ───────────────────────────────────────────────────────────────

    def parse(self) -> Any:
        node = self._expr(0)
        if self._peek().type is not TT.EOF:
            t = self._peek()
            raise FormulaError(
                f"Unexpected token '{t.value}' at position {t.pos}"
            )
        return node

    def _expr(self, min_prec: int) -> Any:
        self._descend()
        try:
            left = self._unary()
            chain = 0
            while self._prec() > min_prec:
                chain += 1
                if chain > MAX_OPERATOR_CHAIN:
                    # The loop itself costs no stack, but each turn wraps the
                    # tree built so far in another node, and the evaluator
                    # descends that spine one frame at a time.
                    raise FormulaError(
                        f"Too many operators in a row, the limit is "
                        f"{MAX_OPERATOR_CHAIN}"
                    )
                op_tok = self._advance()
                # Right-associative for ^ : allow equal precedence on the right
                rp = _PREC[op_tok.type]
                if op_tok.type is TT.CARET:
                    rp -= 1
                right = self._expr(rp)
                left = BinOp(op_tok.value, left, right)
            return left
        finally:
            self._depth -= 1

    def _unary(self) -> Any:
        """
        Parse a run of prefix operators and the operand they apply to.

        Written as a loop rather than by recursing on itself for two reasons.
        It costs no interpreter stack, so a long run of prefix operators cannot
        exhaust it. And it keeps the depth counter in ``_expr`` meaning what it
        says: one unit per level of grouping, rather than two.

        The run is still bounded, because the evaluator walks the resulting
        chain of nodes recursively even though building it no longer does.
        """
        ops: list[str] = []
        while True:
            if self._match(TT.MINUS):
                self._advance()
                ops.append("-")
            elif self._match(TT.NOT):
                self._advance()
                ops.append("not")
            else:
                break
            if len(ops) > MAX_PARSE_DEPTH:
                raise FormulaError(
                    f"Too many prefix operators in a row, the limit is "
                    f"{MAX_PARSE_DEPTH}"
                )

        node = self._primary()
        for op in reversed(ops):
            node = UnaryOp(op, node)
        return node

    def _primary(self) -> Any:
        t = self._peek()

        if t.type is TT.NUMBER:
            self._advance()
            return NumberLit(t.value)

        if t.type is TT.STRING:
            self._advance()
            return StringLit(t.value)

        if t.type is TT.TRUE:
            self._advance()
            return BoolLit(True)

        if t.type is TT.FALSE:
            self._advance()
            return BoolLit(False)

        if t.type is TT.NULL:
            self._advance()
            return NullLit()

        if t.type is TT.LPAREN:
            self._advance()
            node = self._expr(0)
            self._expect(TT.RPAREN)
            return node

        if t.type is TT.IDENT:
            self._advance()
            if self._match(TT.LPAREN):
                return self._func_call(t)
            raise FormulaError(
                f"Bare identifier '{t.value}' at position {t.pos} — "
                f"did you mean prop('{t.value}')?"
            )

        # 'and' and 'or' are lexed as keyword tokens, not identifiers, but they
        # double as variadic functions — and(a, b, …) / or(a, b, …).  When
        # either keyword is immediately followed by '(' we route through the
        # normal function-call path so _call() can handle them.
        if t.type in (TT.AND, TT.OR):
            nxt = self._tokens[self._pos + 1] if self._pos + 1 < len(self._tokens) else None
            if nxt is not None and nxt.type is TT.LPAREN:
                self._advance()
                return self._func_call(t)

        raise FormulaError(
            f"Unexpected token '{t.value}' ({t.type.name}) at position {t.pos}"
        )

    def _func_call(self, name_tok: Token) -> FuncCall:
        """Parse a function call; name token already consumed."""
        self._expect(TT.LPAREN)
        args: list[Any] = []
        if not self._match(TT.RPAREN):
            args.append(self._expr(0))
            while self._match(TT.COMMA):
                self._advance()
                args.append(self._expr(0))
        self._expect(TT.RPAREN)
        return FuncCall(name_tok.value, args)


# ─── Date helpers ─────────────────────────────────────────────────────────────

def _to_datetime(val: Any, label: str) -> _Datetime:
    """
    Coerce a context value or formula result to an aware ``datetime``.

    Accepts:
    - A ``datetime`` object (naive datetimes are assumed UTC).
    - An ISO 8601 string ("2024-01-15", "2024-01-15T14:30:00Z",
      "2024-01-15T14:30:00+00:00").
    - A date property dict ``{"start": "…", "end": "…"}`` — the start value
      is used.  Callers that want the end value should use ``_to_datetime_end``.
    - A ``_Styled`` wrapper — unwrapped transparently.
    """
    if isinstance(val, _Styled):
        val = val.value
    # Date property dict: {"start": "…", "end": "…"}
    if isinstance(val, dict):
        start = val.get("start")
        if start is None:
            raise FormulaError(f"{label}: date property has no start value")
        return _to_datetime(start, label)
    if isinstance(val, _Datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=_tz.utc)
    if isinstance(val, str):
        clean = val.strip()
        # Full ISO with optional Z / offset.
        # fromisoformat("2024-06-15") returns a naive datetime for date-only
        # strings, so we normalise to UTC when tzinfo is absent.
        try:
            dt = _Datetime.fromisoformat(clean.replace("Z", "+00:00"))
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=_tz.utc)
        except ValueError:
            pass
        # datetime-local format from HTML input: YYYY-MM-DDTHH:MM (no seconds, no tz)
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                return _Datetime.strptime(clean, fmt).replace(tzinfo=_tz.utc)
            except ValueError:
                pass
        # Date-only YYYY-MM-DD
        try:
            return _Datetime.strptime(clean, "%Y-%m-%d").replace(tzinfo=_tz.utc)
        except ValueError:
            pass
        raise FormulaError(f"{label}: cannot parse date string '{clean}'")
    raise FormulaError(f"{label}: expected a date, got {type(val).__name__}")


def _month_add(dt: _Datetime, months: int) -> _Datetime:
    """Add (or subtract) an integer number of months, clamping to end-of-month."""
    total = dt.year * 12 + (dt.month - 1) + months
    year, m0 = divmod(total, 12)
    month = m0 + 1
    last_day = _calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day)
    return dt.replace(year=year, month=month, day=day)


def _date_add(dt: _Datetime, amount: int, unit: str) -> _Datetime:
    """Add *amount* of *unit* to *dt*. Singular and plural unit names accepted."""
    u = unit.lower().rstrip("s")  # "days" -> "day", "months" -> "month", etc.
    if u == "year":
        return _month_add(dt, amount * 12)
    if u == "quarter":
        return _month_add(dt, amount * 3)
    if u == "month":
        return _month_add(dt, amount)
    if u == "week":
        return dt + _timedelta(weeks=amount)
    if u == "day":
        return dt + _timedelta(days=amount)
    if u == "hour":
        return dt + _timedelta(hours=amount)
    if u == "minute":
        return dt + _timedelta(minutes=amount)
    raise FormulaError(
        f"Unknown date unit '{unit}'. "
        "Valid units: years, quarters, months, weeks, days, hours, minutes"
    )


def _whole_months(dt1: _Datetime, dt2: _Datetime) -> int:
    """
    Return the number of whole calendar months from *dt2* to *dt1*.

    The result is truncated toward zero and is negative when *dt1* lies
    before *dt2*. A month counts only once it is complete: the candidate
    count is the difference of the calendar months, and it is reduced by one
    when stepping *dt2* forward (or backward) by that many months with
    :func:`_month_add` overshoots *dt1*.

    Anchoring on :func:`_month_add` keeps ``dateBetween`` the inverse of
    ``dateAdd``, including its end-of-month clamping: from 31 January, one
    month is complete on 29 February in a leap year, and from 29 February,
    one year is complete on 28 February of the following year. Time of day
    and UTC offsets are respected because complete datetimes are compared,
    not individual fields.
    """
    months = (dt1.year * 12 + dt1.month) - (dt2.year * 12 + dt2.month)
    if months > 0 and _month_add(dt2, months) > dt1:
        months -= 1
    elif months < 0 and _month_add(dt2, months) < dt1:
        months += 1
    return months


def _date_diff(dt1: _Datetime, dt2: _Datetime, unit: str) -> int:
    """
    Return the truncated integer difference ``dt1 - dt2`` in *unit*.

    Positive when dt1 is later than dt2. Only completed units are counted,
    so a span of eleven months and thirty days is 0 years. Months, quarters
    and years are calendar-based (see :func:`_whole_months`); the smaller
    units are derived from the exact elapsed time.
    """
    u = unit.lower().rstrip("s")
    delta = dt1 - dt2
    total_seconds = delta.total_seconds()

    if u == "minute":
        return int(total_seconds / 60)
    if u == "hour":
        return int(total_seconds / 3600)
    if u == "day":
        return int(total_seconds / 86400)
    if u == "week":
        return int(total_seconds / (86400 * 7))
    if u == "month":
        return _whole_months(dt1, dt2)
    if u == "quarter":
        # int() truncates toward zero; floor division would round negative
        # spans away from zero.
        return int(_whole_months(dt1, dt2) / 3)
    if u == "year":
        return int(_whole_months(dt1, dt2) / 12)
    raise FormulaError(
        f"Unknown date unit '{unit}'. "
        "Valid units: years, quarters, months, weeks, days, hours, minutes"
    )


# Moment.js-style format token regex (longer tokens must precede shorter ones)
_FMT_TOKENS = re.compile(
    r"YYYY|YY|MMMM|MMM|MM|M|DDDD|DDD|DD|D|HH|H|hh|h|mm|m|ss|s|A|a"
)

_WEEKDAY_FULL  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTH_FULL    = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_MONTH_SHORT   = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _format_date(dt: _Datetime, fmt: str) -> str:
    """Format *dt* using Moment.js-style tokens."""
    def _replace(m: re.Match) -> str:
        tok = m.group()
        if tok == "YYYY": return f"{dt.year:04d}"
        if tok == "YY":   return f"{dt.year % 100:02d}"
        if tok == "MMMM": return _MONTH_FULL[dt.month - 1]
        if tok == "MMM":  return _MONTH_SHORT[dt.month - 1]
        if tok == "MM":   return f"{dt.month:02d}"
        if tok == "M":    return str(dt.month)
        if tok == "DDDD": return _WEEKDAY_FULL[dt.weekday()]
        if tok == "DDD":  return _WEEKDAY_SHORT[dt.weekday()]
        if tok == "DD":   return f"{dt.day:02d}"
        if tok == "D":    return str(dt.day)
        if tok == "HH":   return f"{dt.hour:02d}"
        if tok == "H":    return str(dt.hour)
        if tok == "hh":   h = dt.hour % 12 or 12; return f"{h:02d}"
        if tok == "h":    return str(dt.hour % 12 or 12)
        if tok == "mm":   return f"{dt.minute:02d}"
        if tok == "m":    return str(dt.minute)
        if tok == "ss":   return f"{dt.second:02d}"
        if tok == "s":    return str(dt.second)
        if tok == "A":    return "AM" if dt.hour < 12 else "PM"
        if tok == "a":    return "am" if dt.hour < 12 else "pm"
        return tok  # unreachable
    return _FMT_TOKENS.sub(_replace, fmt)


def _try_datetime(v: Any) -> Optional[_Datetime]:
    """
    Attempt to coerce *v* to an aware ``datetime``; return ``None`` on failure.

    Accepts the same inputs as ``_to_datetime`` (datetime objects, ISO strings,
    date-property dicts) and silently returns ``None`` for values that cannot
    be interpreted as dates (numbers, booleans, …).

    Used by the ordered comparison operators (``<``, ``<=``, ``>``, ``>=``)
    so that date comparisons work natively without requiring ``timestamp()``.
    """
    if isinstance(v, _Styled):
        v = v.value
    if isinstance(v, _Datetime):
        return v if v.tzinfo is not None else v.replace(tzinfo=_tz.utc)
    if isinstance(v, dict):
        start = v.get("start")
        if start:
            try:
                return _to_datetime(start, "comparison")
            except FormulaError:
                return None
        return None
    if isinstance(v, str):
        try:
            return _to_datetime(v, "comparison")
        except FormulaError:
            return None
    return None


# ─── Evaluator ────────────────────────────────────────────────────────────────


def _is_empty(v: Any) -> bool:
    """
    Return ``True`` when *v* represents an empty source value.

    Empty means ``None`` (an unset number/date property) or the empty string
    ``""`` (an unset text property). Used by the arithmetic operators and the
    unary numeric functions to decide whether *all* of their inputs are empty —
    in which case the result is relayed as empty rather than collapsed to ``0``.
    """
    if isinstance(v, _Styled):
        v = v.value
    return v is None or v == ""


def _require_num(v: Any, ctx: str) -> float | int:
    """
    Coerce *v* to a number for use in a numeric context.

    Empty values — ``None`` and the empty string ``""`` — are treated as ``0``.
    This is the *mixed* case: an empty operand combined with a concrete value
    counts as zero, so ``prop("A") + prop("B")`` with an empty ``B`` yields
    ``A`` instead of surfacing a type error. The decision about whether *all*
    operands are empty (and the result should therefore stay empty) is made by
    the caller via :func:`_is_empty` before this coercion runs.

    Genuine non-numeric values (non-empty strings, booleans, …) still raise a
    ``FormulaError`` so that real type mistakes remain visible.
    """
    if isinstance(v, _Styled):
        v = v.value
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        raise FormulaError(f"{ctx}: expected number, got boolean")
    if isinstance(v, (int, float)):
        return v
    raise FormulaError(f"{ctx}: expected number, got {type(v).__name__}")


def _checked_power(base: float | int, exponent: float | int) -> float | int:
    """
    Raise *base* to *exponent* within bounds, or fail with a formula error.

    Python happily evaluates ``9 ** 387420489``, which is what ``9^9^9`` asks
    for once the right-associative parse is done: three characters of input,
    and the process is gone. Two bounds are needed rather than one, because
    the base can be the result of a previous power, so a modest exponent still
    compounds across a chain.

    The size check estimates the decimal length of the result instead of
    computing it, since computing it is exactly what has to be avoided.
    """
    if abs(exponent) > MAX_POWER_EXPONENT:
        raise FormulaError(
            f"^: exponent {exponent} is out of range, the limit is "
            f"{MAX_POWER_EXPONENT}"
        )

    magnitude = abs(base)
    if magnitude > 1 and exponent > 0:
        digits = exponent * math.log10(magnitude)
        if digits > MAX_POWER_RESULT_DIGITS:
            raise FormulaError(
                f"^: the result would have about {int(digits)} digits, "
                f"the limit is {MAX_POWER_RESULT_DIGITS}"
            )

    try:
        return base ** exponent
    except ZeroDivisionError:
        raise FormulaError("^: zero cannot be raised to a negative power")
    except OverflowError:
        raise FormulaError("^: the result is too large")


def _eval(node: Any, ctx: dict[str, FormulaValue]) -> FormulaValue:  # noqa: PLR0911,PLR0912
    if isinstance(node, NumberLit):
        return node.value

    if isinstance(node, StringLit):
        return node.value

    if isinstance(node, BoolLit):
        return node.value

    if isinstance(node, NullLit):
        return None

    if isinstance(node, UnaryOp):
        val = _eval(node.operand, ctx)
        if node.op == "-":
            if _is_empty(val):
                return None
            return -_require_num(val, "negation")
        if node.op == "not":
            return not bool(val)
        raise FormulaError(f"Unknown unary operator '{node.op}'")

    if isinstance(node, BinOp):
        op = node.op

        # Short-circuit logical operators
        if op == "and":
            left = _eval(node.left, ctx)
            return left if not left else _eval(node.right, ctx)
        if op == "or":
            left = _eval(node.left, ctx)
            return left if left else _eval(node.right, ctx)

        left = _eval(node.left, ctx)
        right = _eval(node.right, ctx)

        # Unwrap styled wrappers once for value inspection.
        lv = left.value if isinstance(left, _Styled) else left
        rv = right.value if isinstance(right, _Styled) else right

        if op == "+":
            if isinstance(lv, str) or isinstance(rv, str):
                return (str(lv) if lv is not None else "") + (
                    str(rv) if rv is not None else ""
                )
            # All operands empty -> relay empty; a single empty side -> 0.
            if _is_empty(lv) and _is_empty(rv):
                return None
            return _require_num(left, "+") + _require_num(right, "+")
        if op == "-":
            if _is_empty(lv) and _is_empty(rv):
                return None
            return _require_num(left, "-") - _require_num(right, "-")
        if op == "*":
            if _is_empty(lv) and _is_empty(rv):
                return None
            return _require_num(left, "*") * _require_num(right, "*")
        if op == "/":
            if _is_empty(lv) and _is_empty(rv):
                return None
            r = _require_num(right, "/")
            if r == 0:
                raise FormulaError("Division by zero")
            return _require_num(left, "/") / r
        if op == "%":
            if _is_empty(lv) and _is_empty(rv):
                return None
            r = _require_num(right, "%")
            if r == 0:
                raise FormulaError("Modulo by zero")
            return _require_num(left, "%") % r
        if op == "^":
            if _is_empty(lv) and _is_empty(rv):
                return None
            return _checked_power(_require_num(left, "^"), _require_num(right, "^"))
        if op == "==":
            return lv == rv
        if op == "!=":
            return lv != rv
        # Ordered comparisons: try datetime first, fall back to numeric.
        if op in ("<", "<=", ">", ">="):
            lv = left.value if isinstance(left, _Styled) else left
            rv = right.value if isinstance(right, _Styled) else right
            l_dt = _try_datetime(lv)
            r_dt = _try_datetime(rv)
            if l_dt is not None and r_dt is not None:
                if op == "<":  return l_dt < r_dt
                if op == "<=": return l_dt <= r_dt
                if op == ">":  return l_dt > r_dt
                if op == ">=": return l_dt >= r_dt
            if op == "<":  return _require_num(left, "<")  < _require_num(right, "<")
            if op == "<=": return _require_num(left, "<=") <= _require_num(right, "<=")
            if op == ">":  return _require_num(left, ">")  > _require_num(right, ">")
            if op == ">=": return _require_num(left, ">=") >= _require_num(right, ">=")
        raise FormulaError(f"Unknown binary operator '{op}'")

    if isinstance(node, FuncCall):
        return _call(node, ctx)

    raise FormulaError(f"Unknown AST node type: {type(node).__name__}")


# ─── Function registry ────────────────────────────────────────────────────────
#
# Every function a formula can call is registered here with its handler and the
# metadata the formula editor's syntax reference shows. ``_call`` dispatches
# through the registry, ``validate_syntax`` checks names against it, and
# ``syntax_catalogue`` hands the editor the same list, so the three can never
# disagree about which functions exist.
#
# A handler receives the unevaluated argument nodes and the property context;
# it evaluates what it needs, which is how ``if`` and the logical functions
# short-circuit.

Handler = Callable[[list[Any], dict[str, FormulaValue]], FormulaValue]

# Categories of the syntax reference, in display order.
SYNTAX_CATEGORIES: tuple[str, ...] = ("basics", "logic", "math", "text", "list", "date", "operator")


@dataclass(frozen=True)
class FunctionSpec:
    """A formula function: how it is called and what the reference shows."""

    name: str
    category: str
    signature: str
    # Text the editor inserts for the function; empty when there is no useful
    # skeleton to insert.
    insert: str
    handler: Handler


_FUNCTIONS: dict[str, FunctionSpec] = {}


def _function(name: str, category: str, signature: str, insert: str = "") -> Callable[[Handler], Handler]:
    """Register the decorated handler as the function *name* (case-insensitive)."""
    def register(handler: Handler) -> Handler:
        _FUNCTIONS[name.lower()] = FunctionSpec(name, category, signature, insert, handler)
        return handler
    return register


def _call(node: FuncCall, ctx: dict[str, FormulaValue]) -> FormulaValue:
    spec = _FUNCTIONS.get(node.name.lower())
    if spec is None:
        raise FormulaError(f"Unknown function '{node.name}'")
    return spec.handler(node.args, ctx)


def _unstyled(value: FormulaValue) -> FormulaValue:
    """*value* without a ``style()`` wrapper."""
    return value.value if isinstance(value, _Styled) else value


# ── Property access ───────────────────────────────────────────────────────────


@_function("prop", "basics", "prop('Property')", "prop('')")
def _fn_prop(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 1:
        raise FormulaError("prop() requires exactly one string argument")
    key = _eval(args[0], ctx)
    if not isinstance(key, str):
        raise FormulaError("prop() argument must be a string literal")
    return ctx.get(key)


# ── Logic / control flow ──────────────────────────────────────────────────────


@_function("if", "logic", "if(condition, thenValue, elseValue)", "if(, , )")
def _fn_if(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 3:
        raise FormulaError("if(condition, then_value, else_value) requires 3 arguments")
    return _eval(args[1] if _eval(args[0], ctx) else args[2], ctx)


@_function("ifs", "logic", "ifs(cond1, val1, cond2, val2, …, default)", "ifs(, , )")
def _fn_ifs(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) < 2:
        raise FormulaError(
            "ifs() requires at least 2 arguments (one condition-value pair)"
        )
    i = 0
    while i + 1 < len(args):
        if _eval(args[i], ctx):
            return _eval(args[i + 1], ctx)
        i += 2
    # Trailing single argument is the default value
    if i < len(args):
        return _eval(args[i], ctx)
    return None


@_function("and", "logic", "and(a, b, …)", "and(, )")
def _fn_and(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) < 2:
        raise FormulaError("and() requires at least two arguments")
    result: FormulaValue = _eval(args[0], ctx)
    for a in args[1:]:
        if not result:
            return result
        result = _eval(a, ctx)
    return result


@_function("or", "logic", "or(a, b, …)", "or(, )")
def _fn_or(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) < 2:
        raise FormulaError("or() requires at least two arguments")
    result: FormulaValue = _eval(args[0], ctx)
    for a in args[1:]:
        if result:
            return result
        result = _eval(a, ctx)
    return result


@_function("not", "logic", "not(value)", "not()")
def _fn_not(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    # ``not`` is lexed as a keyword, so ``not(x)`` normally parses as the unary
    # operator; the function stays registered for a call that reaches here.
    if len(args) != 1:
        raise FormulaError("not() requires exactly one argument")
    return not bool(_eval(args[0], ctx))


@_function("empty", "logic", "empty(value)", "empty()")
def _fn_empty(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 1:
        raise FormulaError("empty() requires exactly one argument")
    val = _unstyled(_eval(args[0], ctx))
    if val is None or val == "":
        return True
    # A relation prop returns a list of related IDs; an empty list means
    # no entries are linked.  A show_original rollup also returns a list.
    if isinstance(val, (list, dict)):
        return len(val) == 0
    return False


@_function("equal", "logic", "equal(a, b)", "equal(, )")
def _fn_equal(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    # Notion-compatible alias for the == operator.
    if len(args) != 2:
        raise FormulaError("equal() requires exactly two arguments")
    return _unstyled(_eval(args[0], ctx)) == _unstyled(_eval(args[1], ctx))


# ── Math ──────────────────────────────────────────────────────────────────────


def _unary_number(args: list[Any], ctx: dict[str, FormulaValue], name: str) -> Optional[float | int]:
    """The single numeric argument of *name*, or None when it is empty."""
    if len(args) != 1:
        raise FormulaError(f"{name}() requires exactly one argument")
    v = _eval(args[0], ctx)
    if _is_empty(v):
        return None
    return _require_num(v, name)


@_function("abs", "math", "abs(x)", "abs()")
def _fn_abs(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    v = _unary_number(args, ctx, "abs")
    return None if v is None else abs(v)


@_function("round", "math", "round(x, digits)", "round(, 0)")
def _fn_round(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) not in (1, 2):
        raise FormulaError("round() requires 1 or 2 arguments")
    v = _eval(args[0], ctx)
    if _is_empty(v):
        return None
    val = _require_num(v, "round")
    digits = int(_require_num(_eval(args[1], ctx), "round digits")) if len(args) == 2 else 0
    result = round(val, digits)
    return int(result) if digits == 0 else result


@_function("ceil", "math", "ceil(x)", "ceil()")
def _fn_ceil(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    v = _unary_number(args, ctx, "ceil")
    return None if v is None else math.ceil(v)


@_function("floor", "math", "floor(x)", "floor()")
def _fn_floor(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    v = _unary_number(args, ctx, "floor")
    return None if v is None else math.floor(v)


@_function("divide", "math", "divide(a, b)", "divide(, )")
def _fn_divide(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    # Notion-compatible alias for the / operator.
    if len(args) != 2:
        raise FormulaError("divide(a, b) requires exactly two arguments")
    lv = _eval(args[0], ctx)
    rv = _eval(args[1], ctx)
    if _is_empty(lv) and _is_empty(rv):
        return None
    r = _require_num(rv, "divide")
    if r == 0:
        raise FormulaError("Division by zero")
    return _require_num(lv, "divide") / r


def _aggregate_numbers(args: list[Any], ctx: dict[str, FormulaValue], name: str) -> list[float | int]:
    """
    The numeric values among *args* of the variadic function *name*.

    Empty values (None / "") are skipped so they never skew an aggregate. This
    mirrors the count_empty / count_not_empty rollup split and keeps
    avg/min/max correct when some related entries are unset.
    """
    if len(args) < 1:
        raise FormulaError(f"{name}() requires at least one argument")
    nums: list[float | int] = []
    for a in args:
        v = _unstyled(_eval(a, ctx))
        if v is None or v == "":
            continue
        nums.append(_require_num(v, name))
    return nums


@_function("sum", "math", "sum(a, b, …)", "sum(, )")
def _fn_sum(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    nums = _aggregate_numbers(args, ctx, "sum")
    return sum(nums) if nums else None


@_function("min", "math", "min(a, b, …)", "min(, )")
def _fn_min(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    nums = _aggregate_numbers(args, ctx, "min")
    return min(nums) if nums else None


@_function("max", "math", "max(a, b, …)", "max(, )")
def _fn_max(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    nums = _aggregate_numbers(args, ctx, "max")
    return max(nums) if nums else None


@_function("avg", "math", "avg(a, b, …)", "avg(, )")
def _fn_avg(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    nums = _aggregate_numbers(args, ctx, "avg")
    return sum(nums) / len(nums) if nums else None


@_function("toNumber", "math", "toNumber(value)", "toNumber()")
def _fn_tonumber(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 1:
        raise FormulaError("toNumber() requires exactly one argument")
    val = _unstyled(_eval(args[0], ctx))
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            pass
        try:
            return float(val)
        except ValueError:
            return None
    return None


# ── Text ──────────────────────────────────────────────────────────────────────


@_function("len", "text", "len(text)", "len()")
def _fn_len(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 1:
        raise FormulaError("len() requires exactly one argument")
    val = _unstyled(_eval(args[0], ctx))
    if val is None:
        return 0
    if not isinstance(val, str):
        raise FormulaError(f"len() requires a string, got {type(val).__name__}")
    return len(val)


@_function("concat", "text", "concat(a, b, …)", "concat(, )")
def _fn_concat(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) < 1:
        raise FormulaError("concat() requires at least one argument")
    parts: list[str] = []
    for a in args:
        v = _unstyled(_eval(a, ctx))
        parts.append("" if v is None else str(v))
    return "".join(parts)


def _format_scalar(v: Any) -> str:
    """Format a single scalar value to a string."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    if isinstance(v, _Datetime):
        return v.isoformat()
    return str(v)


@_function("format", "text", "format(value)", "format()")
def _fn_format(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 1:
        raise FormulaError("format() requires exactly one argument")
    val = _unstyled(_eval(args[0], ctx))
    if val is None:
        return ""
    # List: from show_original rollup or relation prop.
    # Single-element lists are unwrapped so comparisons like
    #   format(prop("Status")) == "Done"   work naturally.
    # Multi-element lists are joined with ", ".
    # None elements (missing values) are skipped.
    if isinstance(val, list):
        parts = [_format_scalar(v) for v in val if v is not None]
        return parts[0] if len(parts) == 1 else ", ".join(parts)
    # Dict: from percent_per_option.  Rendered as "Key: val" pairs.
    if isinstance(val, dict):
        if not val:
            return ""
        return ", ".join(f"{k}: {_format_scalar(v)}" for k, v in val.items())
    return _format_scalar(val)


@_function("contains", "text", "contains(text, search)", "contains(, '')")
def _fn_contains(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 2:
        raise FormulaError("contains() requires exactly two arguments")
    text = _unstyled(_eval(args[0], ctx))
    sub = _unstyled(_eval(args[1], ctx))
    if text is None or sub is None:
        return False
    return str(sub) in str(text)


@_function("style", "text", "style(value, 'hint', …)", "style(, '')")
def _fn_style(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) < 2:
        raise FormulaError(
            "style() requires at least two arguments: style(value, hint, …)"
        )
    val = _eval(args[0], ctx)
    # Merge with any existing styles if the value is already styled.
    if isinstance(val, _Styled):
        existing = list(val.styles)
        val = val.value
    else:
        existing = []
    hints: list[str] = []
    for a in args[1:]:
        h = _eval(a, ctx)
        if not isinstance(h, str):
            raise FormulaError("style() hints must be string literals")
        hints.append(h)
    return _Styled(value=val, styles=existing + hints)


@_function("unstyle", "text", "unstyle(value)", "unstyle()")
def _fn_unstyle(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 1:
        raise FormulaError("unstyle() requires exactly one argument")
    return _unstyled(_eval(args[0], ctx))


# ── List access ───────────────────────────────────────────────────────────────


@_function("at", "list", "at(list, index)", "at(, 0)")
def _fn_at(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 2:
        raise FormulaError("at(list, index) requires exactly two arguments")
    lst = _unstyled(_eval(args[0], ctx))
    idx_val = _unstyled(_eval(args[1], ctx))
    if lst is None:
        return None
    if not isinstance(lst, list):
        raise FormulaError(
            f"at(): first argument must be a list, got {type(lst).__name__}"
        )
    if idx_val is None:
        return None
    idx = int(_require_num(idx_val, "at index"))
    if idx < -len(lst) or idx >= len(lst):
        return None
    return lst[idx]


# ── Date & time ───────────────────────────────────────────────────────────────


@_function("now", "date", "now()", "now()")
def _fn_now(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 0:
        raise FormulaError("now() takes no arguments")
    return _Datetime.now(_tz.utc)


@_function("today", "date", "today()", "today()")
def _fn_today(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 0:
        raise FormulaError("today() takes no arguments")
    return _Datetime.now(_tz.utc).replace(hour=0, minute=0, second=0, microsecond=0)


@_function("dateStart", "date", "dateStart(date)", "dateStart()")
def _fn_datestart(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 1:
        raise FormulaError("dateStart() requires exactly one argument")
    val = _eval(args[0], ctx)
    # Dict shape from a date property: extract start
    if isinstance(val, dict):
        start = val.get("start")
        if start is None:
            raise FormulaError("dateStart(): date property has no start value")
        return _to_datetime(start, "dateStart")
    # Plain string or datetime: treat as the start itself
    return _to_datetime(val, "dateStart")


@_function("dateEnd", "date", "dateEnd(date)", "dateEnd()")
def _fn_dateend(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 1:
        raise FormulaError("dateEnd() requires exactly one argument")
    val = _eval(args[0], ctx)
    # Dict shape from a date property: extract end (may be None)
    if isinstance(val, dict):
        end = val.get("end")
        if end is None:
            return None
        return _to_datetime(end, "dateEnd")
    # Plain string or datetime: no end concept, return None
    return None


@_function("parseDate", "date", "parseDate(text)", "parseDate('')")
def _fn_parsedate(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 1:
        raise FormulaError("parseDate() requires exactly one argument")
    raw = _eval(args[0], ctx)
    if not isinstance(raw, str):
        raise FormulaError(
            f"parseDate() requires a string, got {type(raw).__name__}"
        )
    return _to_datetime(raw, "parseDate")


def _shifted_date(args: list[Any], ctx: dict[str, FormulaValue], name: str, sign: int) -> FormulaValue:
    """``dateAdd`` (*sign* 1) and ``dateSubtract`` (*sign* -1)."""
    if len(args) != 3:
        raise FormulaError(f"{name}(date, number, unit) requires 3 arguments")
    dt = _to_datetime(_eval(args[0], ctx), name)
    amount = int(_require_num(_eval(args[1], ctx), f"{name} amount"))
    unit = _eval(args[2], ctx)
    if not isinstance(unit, str):
        raise FormulaError(f"{name}: unit must be a string")
    return _date_add(dt, sign * amount, unit)


@_function("dateAdd", "date", "dateAdd(date, amount, unit)", "dateAdd(, 1, 'days')")
def _fn_dateadd(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    return _shifted_date(args, ctx, "dateAdd", 1)


@_function("dateSubtract", "date", "dateSubtract(date, amount, unit)", "dateSubtract(, 1, 'days')")
def _fn_datesubtract(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    return _shifted_date(args, ctx, "dateSubtract", -1)


@_function("dateBetween", "date", "dateBetween(date1, date2, unit)", "dateBetween(, , 'days')")
def _fn_datebetween(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 3:
        raise FormulaError("dateBetween(date1, date2, unit) requires 3 arguments")
    dt1 = _to_datetime(_eval(args[0], ctx), "dateBetween date1")
    dt2 = _to_datetime(_eval(args[1], ctx), "dateBetween date2")
    unit = _eval(args[2], ctx)
    if not isinstance(unit, str):
        raise FormulaError("dateBetween: unit must be a string")
    return _date_diff(dt1, dt2, unit)


@_function("formatDate", "date", "formatDate(date, format)", "formatDate(, 'DD.MM.YYYY')")
def _fn_formatdate(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    if len(args) != 2:
        raise FormulaError("formatDate(date, format) requires 2 arguments")
    dt = _to_datetime(_eval(args[0], ctx), "formatDate")
    fmt = _eval(args[1], ctx)
    if not isinstance(fmt, str):
        raise FormulaError("formatDate: format must be a string")
    return _format_date(dt, fmt)


def _date_part(args: list[Any], ctx: dict[str, FormulaValue], name: str) -> _Datetime:
    """The single date argument of the date-part function *name*."""
    if len(args) != 1:
        raise FormulaError(f"{name}() requires exactly one argument")
    return _to_datetime(_eval(args[0], ctx), name)


@_function("date", "date", "date(date)", "date()")
def _fn_date(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    return _date_part(args, ctx, "date").day


@_function("month", "date", "month(date)", "month()")
def _fn_month(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    return _date_part(args, ctx, "month").month


@_function("year", "date", "year(date)", "year()")
def _fn_year(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    return _date_part(args, ctx, "year").year


@_function("day", "date", "day(date)", "day()")
def _fn_day(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    return _date_part(args, ctx, "day").weekday() + 1   # 1 = Monday, 7 = Sunday


@_function("hour", "date", "hour(date)", "hour()")
def _fn_hour(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    return _date_part(args, ctx, "hour").hour


@_function("minute", "date", "minute(date)", "minute()")
def _fn_minute(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    return _date_part(args, ctx, "minute").minute


@_function("week", "date", "week(date)", "week()")
def _fn_week(args: list[Any], ctx: dict[str, FormulaValue]) -> FormulaValue:
    return _date_part(args, ctx, "week").isocalendar()[1]


# Names of the registered functions, lower-case, as ``validate_syntax`` checks
# them.
_KNOWN_FUNCTIONS: frozenset[str] = frozenset(_FUNCTIONS)


# ─── Syntax reference ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SyntaxEntry:
    """
    One entry of the formula editor's syntax reference.

    ``key`` names the entry for its translated description and example in the
    frontend locales (``db.formulaHelp.<key>``): the lower-case function name,
    or a fixed key for the parts of the syntax that are no function.
    """

    key: str
    name: str
    category: str
    signature: str
    insert: str


# Syntax that is no function: literals, grouping and the operators.
_SYNTAX_ELEMENTS: tuple[SyntaxEntry, ...] = (
    SyntaxEntry("literals", "Literals", "basics", "42  3.5  'text'  \"text\"  true  false  null", ""),
    SyntaxEntry("grouping", "( )", "basics", "(a + b) * c", ""),
    SyntaxEntry("arithmetic", "+ - * /", "operator", "a + b  |  a - b  |  a * b  |  a / b", ""),
    SyntaxEntry("text_concat", "+ (text)", "operator", "'text' + value", ""),
    SyntaxEntry("modulo", "%", "operator", "a % b", "% "),
    SyntaxEntry("power", "^", "operator", "a ^ b", "^ "),
    SyntaxEntry("negation", "- (unary)", "operator", "-a", ""),
    SyntaxEntry("comparison", "== != < <= > >=", "operator", "a == b  |  a != b  |  a < b  |  a >= b", ""),
    SyntaxEntry("logical", "and or not", "operator", "a and b  |  a or b  |  not a", ""),
)


def syntax_catalogue() -> list[SyntaxEntry]:
    """
    Every entry of the syntax reference: the registered functions, then the
    rest of the syntax, ordered by category (``SYNTAX_CATEGORIES``) and, within
    one, by registration.
    """
    functions = [
        SyntaxEntry(key, spec.name, spec.category, spec.signature, spec.insert)
        for key, spec in _FUNCTIONS.items()
    ]
    entries = functions + list(_SYNTAX_ELEMENTS)
    order = {category: index for index, category in enumerate(SYNTAX_CATEGORIES)}
    return sorted(entries, key=lambda e: order[e.category])


def _check_known_functions(node: Any) -> None:
    """Recursively verify that every FuncCall in the AST names a known function.

    Called by ``validate_syntax`` so that the modal catches unknown function
    names at validation time rather than silently failing at evaluation time.
    """
    if isinstance(node, FuncCall):
        if node.name.lower() not in _KNOWN_FUNCTIONS:
            raise FormulaError(f"Unknown function '{node.name}'")
        for arg in node.args:
            _check_known_functions(arg)
    elif isinstance(node, BinOp):
        _check_known_functions(node.left)
        _check_known_functions(node.right)
    elif isinstance(node, UnaryOp):
        _check_known_functions(node.operand)
    # Literals (NumberLit, StringLit, BoolLit, NullLit) have no children.


# ─── AST visitor for prop() extraction ───────────────────────────────────────


def _walk_props(node: Any, names: list[str], seen: set[str]) -> None:
    """Depth-first walk collecting property names from prop('…') calls."""
    if isinstance(node, FuncCall):
        if node.name.lower() == "prop" and len(node.args) == 1 and isinstance(
            node.args[0], StringLit
        ):
            name = node.args[0].value
            if name not in seen:
                seen.add(name)
                names.append(name)
        for arg in node.args:
            _walk_props(arg, names, seen)
    elif isinstance(node, BinOp):
        _walk_props(node.left, names, seen)
        _walk_props(node.right, names, seen)
    elif isinstance(node, UnaryOp):
        _walk_props(node.operand, names, seen)
    # Literals have no children


# Functions whose result changes with the time of evaluation rather than with
# the values a formula reads.
_VOLATILE_FUNCTIONS = frozenset({"now", "today"})


def _calls_volatile(node: Any) -> bool:
    """Depth-first search for a call of one of ``_VOLATILE_FUNCTIONS``."""
    if isinstance(node, FuncCall):
        if node.name.lower() in _VOLATILE_FUNCTIONS:
            return True
        return any(_calls_volatile(arg) for arg in node.args)
    if isinstance(node, BinOp):
        return _calls_volatile(node.left) or _calls_volatile(node.right)
    if isinstance(node, UnaryOp):
        return _calls_volatile(node.operand)
    return False


# ─── Public API ───────────────────────────────────────────────────────────────


def evaluate(expression: str, context: dict[str, FormulaValue]) -> FormulaResult:
    """
    Evaluate *expression* with the given property *context*.

    Parameters
    ----------
    expression:
        The formula string, e.g. ``"prop('Price') * prop('Quantity')"``.
    context:
        Mapping from property name to its scalar value for a single entry.
        Values are expected to be pre-resolved by the caller (numbers, strings,
        booleans, datetime objects, ISO date strings, or None).

    Returns
    -------
    FormulaResult
        ``.result`` contains the computed scalar (None on error).
        ``.error`` is a human-readable message when evaluation failed.
        ``.style`` is a list of Notion-style hints (e.g. ``["red", "b"]``)
        when the top-level expression is wrapped in ``style()``.
    """
    try:
        tokens = _lex(expression)
        ast = _Parser(tokens).parse()
        result = _eval(ast, context)
        if isinstance(result, _Styled):
            return FormulaResult(result=result.value, style=result.styles)
        return FormulaResult(result=result)
    except FormulaError as exc:
        return FormulaResult(result=None, error=str(exc))
    except ZeroDivisionError:
        return FormulaResult(result=None, error="Division by zero")
    except RecursionError:
        return FormulaResult(result=None, error="Expression is nested too deeply")
    except Exception as exc:
        return FormulaResult(result=None, error=f"Runtime error: {exc}")


def extract_prop_names(expression: str) -> list[str]:
    """
    Return the list of property names referenced via ``prop('Name')`` in
    *expression*, in first-occurrence order.

    Used by the dependency-graph builder for cycle detection.

    Raises ``FormulaError`` on syntax errors, and on nothing else: callers
    treat a bad formula as a recoverable condition, so anything the parser
    might throw is converted rather than allowed to reach them as a 500.
    """
    try:
        tokens = _lex(expression)
        ast = _Parser(tokens).parse()
        names: list[str] = []
        seen: set[str] = set()
        _walk_props(ast, names, seen)
        return names
    except FormulaError:
        raise
    except RecursionError:
        raise FormulaError("Expression is nested too deeply") from None


def uses_volatile_functions(expression: str) -> bool:
    """
    Return whether *expression* calls a function whose result depends on the
    time of evaluation (``now()``, ``today()``).

    Such a formula can change without any property it reads changing, so the
    computed-property engine evaluates it whenever it evaluates its entry at
    all. An expression that cannot be parsed counts as not volatile: it only
    ever yields its error.
    """
    try:
        tokens = _lex(expression)
        ast = _Parser(tokens).parse()
        return _calls_volatile(ast)
    except FormulaError:
        return False
    except RecursionError:
        return False


def _string_literal_end(src: str, start: int) -> int:
    """
    Return the index just past the closing quote of the string literal that
    begins at *start* (the opening-quote index), honouring backslash escapes —
    mirroring the scanning rules of :func:`_lex`.
    """
    quote = src[start]
    j = start + 1
    n = len(src)
    while j < n and src[j] != quote:
        j += 2 if (src[j] == "\\" and j + 1 < n) else 1
    return j + 1  # past the closing quote


def _encode_string_literal(value: str, quote: str) -> str:
    """
    Encode *value* as a quoted string literal delimited by *quote*, escaping the
    backslash, the quote character, and the control characters the lexer maps,
    so the result re-lexes back to *value*.
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace(quote, "\\" + quote)
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f"{quote}{escaped}{quote}"


def map_prop_references(expression: str, mapping: Mapping[str, str]) -> str:
    """
    Return *expression* with the argument of every ``prop('…')`` call that
    appears in *mapping* replaced by its mapped value, leaving all other string
    literals and the surrounding formatting untouched.

    This is how the references of a formula move between the form it is stored
    in (property ids) and the form the editor shows (property names), in both
    directions. Only a string literal that sits immediately after a ``prop``
    identifier and its opening parenthesis is rewritten, so a literal that
    merely equals a mapped value elsewhere, a compared value
    ``== "Status"`` say, is left alone. Matching is exact (case- and
    whitespace-sensitive), as ``prop()`` resolves at evaluation time. A
    ``prop()`` whose argument is computed (``prop(concat('A', 'B'))``) has no
    literal to rewrite and stays as it is.

    The original quote character is kept and the new value re-encoded for it.
    A malformed expression that no longer lexes is returned unchanged, so
    translating never fails because of a broken formula.
    """
    if not expression or not mapping:
        return expression
    try:
        tokens = _lex(expression)
    except FormulaError:
        return expression

    # Collect (start, end, replacement) spans for matching prop() string args.
    spans: list[tuple[int, int, str]] = []
    for i in range(len(tokens) - 2):
        name_tok, lparen_tok, str_tok = tokens[i], tokens[i + 1], tokens[i + 2]
        if (
            name_tok.type is TT.IDENT
            and name_tok.value.lower() == "prop"
            and lparen_tok.type is TT.LPAREN
            and str_tok.type is TT.STRING
            and str_tok.value in mapping
            and mapping[str_tok.value] != str_tok.value
        ):
            start = str_tok.pos
            end = _string_literal_end(expression, start)
            quote = expression[start]
            spans.append((start, end, _encode_string_literal(mapping[str_tok.value], quote)))

    if not spans:
        return expression

    # Splice from right to left so earlier positions stay valid.
    out = expression
    for start, end, literal in sorted(spans, key=lambda s: s[0], reverse=True):
        out = out[:start] + literal + out[end:]
    return out


def validate_syntax(expression: str) -> None:
    """
    Parse *expression* without evaluating it, then verify that every function
    call names a known built-in.

    Raises ``FormulaError`` if the expression contains a syntax error or
    references an unknown function (e.g. ``empty`` before it was implemented).
    This ensures the formula modal gives accurate feedback at edit time instead
    of accepting expressions that will fail silently at runtime.

    ``FormulaError`` is the only thing this raises. The depth cap in the parser
    already prevents the stack from running out, so the ``RecursionError`` arm
    below is a second line of defence for the recursive walks that follow the
    parse.
    """
    try:
        tokens = _lex(expression)
        ast = _Parser(tokens).parse()
        _check_known_functions(ast)
    except FormulaError:
        raise
    except RecursionError:
        raise FormulaError("Expression is nested too deeply") from None
