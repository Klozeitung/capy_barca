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
Functions       : abs(x)  round(x[, digits])  ceil(x)  floor(x)
                  sum(a, …)  min(a, …)  max(a, …)  avg(a, …)
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

formatDate tokens (Moment.js-style)
------------------------------------
  YYYY YY  MMMM MMM MM M  DDDD DDD DD D
  HH H  hh h  mm m  ss s  A a

Operator precedence (high → low)
---------------------------------
  ^  >  unary  >  * / %  >  + -  >  comparisons  >  not  >  and  >  or

Public API
----------
  evaluate(expression, context)   -> FormulaResult
  extract_prop_names(expression)  -> list[str]
  validate_syntax(expression)     -> None  (raises FormulaError on error)

Resource limits
---------------
Formulas are written by authenticated users and run on the server, so an
expression is bounded before it is allowed to consume anything:

  * length          – an expression past ``MAX_EXPRESSION_LENGTH`` characters
                      is refused by the lexer, before a parser ever sees it.
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

All three raise ``FormulaError``, so every caller that already handles a bad
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
from typing import Any, Optional

# ─── Public types ─────────────────────────────────────────────────────────────

FormulaValue = Any  # number | str | bool | datetime | None


# ── Resource limits ───────────────────────────────────────────────────────────
# Deliberately generous: a hand-written formula does not come close to any of
# them, while each bounds a way for one expression to occupy the server.

MAX_EXPRESSION_LENGTH = 2000

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
# own recursion limit.
MAX_PARSE_DEPTH = 64
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
    if len(src) > MAX_EXPRESSION_LENGTH:
        raise FormulaError(
            f"Expression is too long: {len(src)} characters, "
            f"the limit is {MAX_EXPRESSION_LENGTH}"
        )

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


def _date_diff(dt1: _Datetime, dt2: _Datetime, unit: str) -> int:
    """
    Return the truncated integer difference ``dt1 - dt2`` in *unit*.

    Positive when dt1 is later than dt2.
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
        return (dt1.year * 12 + dt1.month) - (dt2.year * 12 + dt2.month)
    if u == "quarter":
        months = (dt1.year * 12 + dt1.month) - (dt2.year * 12 + dt2.month)
        return months // 3
    if u == "year":
        return dt1.year - dt2.year
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


def _call(node: FuncCall, ctx: dict[str, FormulaValue]) -> FormulaValue:  # noqa: PLR0911,PLR0912
    name = node.name.lower()
    args = node.args

    # ── Property access ───────────────────────────────────────────────────────

    if name == "prop":
        if len(args) != 1:
            raise FormulaError("prop() requires exactly one string argument")
        key = _eval(args[0], ctx)
        if not isinstance(key, str):
            raise FormulaError("prop() argument must be a string literal")
        return ctx.get(key)

    # ── Logic / control flow ──────────────────────────────────────────────────

    if name == "if":
        if len(args) != 3:
            raise FormulaError("if(condition, then_value, else_value) requires 3 arguments")
        return _eval(args[1] if _eval(args[0], ctx) else args[2], ctx)

    if name == "ifs":
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

    if name == "not":
        if len(args) != 1:
            raise FormulaError("not() requires exactly one argument")
        return not bool(_eval(args[0], ctx))

    if name == "and":
        if len(args) < 2:
            raise FormulaError("and() requires at least two arguments")
        result: FormulaValue = _eval(args[0], ctx)
        for a in args[1:]:
            if not result:
                return result
            result = _eval(a, ctx)
        return result

    if name == "or":
        if len(args) < 2:
            raise FormulaError("or() requires at least two arguments")
        result = _eval(args[0], ctx)
        for a in args[1:]:
            if result:
                return result
            result = _eval(a, ctx)
        return result

    # ── Math ──────────────────────────────────────────────────────────────────

    if name == "abs":
        if len(args) != 1:
            raise FormulaError("abs() requires exactly one argument")
        v = _eval(args[0], ctx)
        if _is_empty(v):
            return None
        return abs(_require_num(v, "abs"))

    if name == "round":
        if len(args) not in (1, 2):
            raise FormulaError("round() requires 1 or 2 arguments")
        v = _eval(args[0], ctx)
        if _is_empty(v):
            return None
        val = _require_num(v, "round")
        digits = int(_require_num(_eval(args[1], ctx), "round digits")) if len(args) == 2 else 0
        result = round(val, digits)
        return int(result) if digits == 0 else result

    if name == "ceil":
        if len(args) != 1:
            raise FormulaError("ceil() requires exactly one argument")
        v = _eval(args[0], ctx)
        if _is_empty(v):
            return None
        return math.ceil(_require_num(v, "ceil"))

    if name == "floor":
        if len(args) != 1:
            raise FormulaError("floor() requires exactly one argument")
        v = _eval(args[0], ctx)
        if _is_empty(v):
            return None
        return math.floor(_require_num(v, "floor"))

    # ── Text ──────────────────────────────────────────────────────────────────

    if name == "len":
        if len(args) != 1:
            raise FormulaError("len() requires exactly one argument")
        val = _eval(args[0], ctx)
        if isinstance(val, _Styled):
            val = val.value
        if val is None:
            return 0
        if not isinstance(val, str):
            raise FormulaError(f"len() requires a string, got {type(val).__name__}")
        return len(val)

    if name == "concat":
        if len(args) < 1:
            raise FormulaError("concat() requires at least one argument")
        parts: list[str] = []
        for a in args:
            v = _eval(a, ctx)
            if isinstance(v, _Styled):
                v = v.value
            parts.append("" if v is None else str(v))
        return "".join(parts)

    if name == "empty":
        if len(args) != 1:
            raise FormulaError("empty() requires exactly one argument")
        val = _eval(args[0], ctx)
        if isinstance(val, _Styled):
            val = val.value
        if val is None or val == "":
            return True
        # A relation prop returns a list of related IDs; an empty list means
        # no entries are linked.  A show_original rollup also returns a list.
        if isinstance(val, (list, dict)):
            return len(val) == 0
        return False

    if name == "format":
        if len(args) != 1:
            raise FormulaError("format() requires exactly one argument")
        val = _eval(args[0], ctx)
        if isinstance(val, _Styled):
            val = val.value

        def _fmt_scalar(v: Any) -> str:
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

        if val is None:
            return ""
        # List: from show_original rollup or relation prop.
        # Single-element lists are unwrapped so comparisons like
        #   format(prop("Status")) == "Done"   work naturally.
        # Multi-element lists are joined with ", ".
        # None elements (missing values) are skipped.
        if isinstance(val, list):
            parts = [_fmt_scalar(v) for v in val if v is not None]
            return parts[0] if len(parts) == 1 else ", ".join(parts)
        # Dict: from percent_per_option.  Rendered as "Key: val" pairs.
        if isinstance(val, dict):
            if not val:
                return ""
            return ", ".join(f"{k}: {_fmt_scalar(v)}" for k, v in val.items())
        return _fmt_scalar(val)

    if name == "tonumber":
        if len(args) != 1:
            raise FormulaError("toNumber() requires exactly one argument")
        val = _eval(args[0], ctx)
        if isinstance(val, _Styled):
            val = val.value
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

    if name == "contains":
        if len(args) != 2:
            raise FormulaError("contains() requires exactly two arguments")
        text = _eval(args[0], ctx)
        sub  = _eval(args[1], ctx)
        if isinstance(text, _Styled): text = text.value
        if isinstance(sub,  _Styled): sub  = sub.value
        if text is None or sub is None:
            return False
        return str(sub) in str(text)

    if name == "style":
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

    if name == "unstyle":
        if len(args) != 1:
            raise FormulaError("unstyle() requires exactly one argument")
        val = _eval(args[0], ctx)
        return val.value if isinstance(val, _Styled) else val

    if name == "equal":
        # Notion-compatible alias for the == operator.
        if len(args) != 2:
            raise FormulaError("equal() requires exactly two arguments")
        lv = _eval(args[0], ctx)
        rv = _eval(args[1], ctx)
        if isinstance(lv, _Styled): lv = lv.value
        if isinstance(rv, _Styled): rv = rv.value
        return lv == rv

    if name == "divide":
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

    # ── Variadic math ─────────────────────────────────────────────────────────

    if name in ("sum", "min", "max", "avg"):
        if len(args) < 1:
            raise FormulaError(f"{name}() requires at least one argument")
        nums: list[float | int] = []
        for a in args:
            v = _eval(a, ctx)
            if isinstance(v, _Styled):
                v = v.value
            # Skip empty values (None / "") so they never skew an aggregate.
            # This mirrors the count_empty / count_not_empty rollup split and
            # keeps avg/min/max correct when some related entries are unset.
            if v is None or v == "":
                continue
            nums.append(_require_num(v, name))
        if not nums:
            return None
        if name == "sum":
            return sum(nums)
        if name == "min":
            return min(nums)
        if name == "max":
            return max(nums)
        if name == "avg":
            return sum(nums) / len(nums)

    # ── List access ───────────────────────────────────────────────────────────

    if name == "at":
        if len(args) != 2:
            raise FormulaError("at(list, index) requires exactly two arguments")
        lst = _eval(args[0], ctx)
        if isinstance(lst, _Styled):
            lst = lst.value
        idx_val = _eval(args[1], ctx)
        if isinstance(idx_val, _Styled):
            idx_val = idx_val.value
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

    # ── Date & time ───────────────────────────────────────────────────────────

    if name == "now":
        if len(args) != 0:
            raise FormulaError("now() takes no arguments")
        return _Datetime.now(_tz.utc)

    if name == "today":
        if len(args) != 0:
            raise FormulaError("today() takes no arguments")
        return _Datetime.now(_tz.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    if name == "datestart":
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

    if name == "dateend":
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

    if name == "parsedate":
        if len(args) != 1:
            raise FormulaError("parseDate() requires exactly one argument")
        raw = _eval(args[0], ctx)
        if not isinstance(raw, str):
            raise FormulaError(
                f"parseDate() requires a string, got {type(raw).__name__}"
            )
        return _to_datetime(raw, "parseDate")

    if name == "dateadd":
        if len(args) != 3:
            raise FormulaError("dateAdd(date, number, unit) requires 3 arguments")
        dt     = _to_datetime(_eval(args[0], ctx), "dateAdd")
        amount = int(_require_num(_eval(args[1], ctx), "dateAdd amount"))
        unit   = _eval(args[2], ctx)
        if not isinstance(unit, str):
            raise FormulaError("dateAdd: unit must be a string")
        return _date_add(dt, amount, unit)

    if name == "datesubtract":
        if len(args) != 3:
            raise FormulaError("dateSubtract(date, number, unit) requires 3 arguments")
        dt     = _to_datetime(_eval(args[0], ctx), "dateSubtract")
        amount = int(_require_num(_eval(args[1], ctx), "dateSubtract amount"))
        unit   = _eval(args[2], ctx)
        if not isinstance(unit, str):
            raise FormulaError("dateSubtract: unit must be a string")
        return _date_add(dt, -amount, unit)

    if name == "datebetween":
        if len(args) != 3:
            raise FormulaError("dateBetween(date1, date2, unit) requires 3 arguments")
        dt1  = _to_datetime(_eval(args[0], ctx), "dateBetween date1")
        dt2  = _to_datetime(_eval(args[1], ctx), "dateBetween date2")
        unit = _eval(args[2], ctx)
        if not isinstance(unit, str):
            raise FormulaError("dateBetween: unit must be a string")
        return _date_diff(dt1, dt2, unit)

    if name == "formatdate":
        if len(args) != 2:
            raise FormulaError("formatDate(date, format) requires 2 arguments")
        dt  = _to_datetime(_eval(args[0], ctx), "formatDate")
        fmt = _eval(args[1], ctx)
        if not isinstance(fmt, str):
            raise FormulaError("formatDate: format must be a string")
        return _format_date(dt, fmt)

    if name in ("date", "month", "year", "day", "hour", "minute", "week"):
        if len(args) != 1:
            raise FormulaError(f"{name}() requires exactly one argument")
        dt = _to_datetime(_eval(args[0], ctx), name)
        if name == "date":    return dt.day
        if name == "month":   return dt.month
        if name == "year":    return dt.year
        if name == "day":     return dt.weekday() + 1   # 1 = Monday, 7 = Sunday
        if name == "hour":    return dt.hour
        if name == "minute":  return dt.minute
        if name == "week":    return dt.isocalendar()[1]

    raise FormulaError(f"Unknown function '{node.name}'")


# ─── Known-function registry (used by validate_syntax) ───────────────────────

_KNOWN_FUNCTIONS: frozenset[str] = frozenset({
    "prop",
    # Logic / control flow
    "if", "ifs", "not", "and", "or",
    # Math
    "abs", "round", "ceil", "floor",
    # Text / utility
    "len", "concat", "empty", "format", "tonumber",
    "contains", "style", "unstyle",
    # Math (variadic)
    "sum", "min", "max", "avg",
    # List access
    "at",
    # Aliases / compatibility (Notion parity)
    "equal", "divide",
    # Date & time
    "now", "today",
    "dateadd", "datesubtract", "datebetween",
    "formatdate", "parsedate",
    "datestart", "dateend",
    "date", "month", "year", "day", "hour", "minute", "week",
})


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


def rename_prop_in_expression(expression: str, old_name: str, new_name: str) -> str:
    """
    Return *expression* with every ``prop("old_name")`` reference rewritten to
    use *new_name*, leaving all other string literals and the surrounding
    formatting untouched.

    Only a string literal that sits immediately after a ``prop`` identifier and
    its opening parenthesis is rewritten, so a literal that merely happens to
    equal *old_name* elsewhere — e.g. a compared value ``== "old_name"`` — is
    left alone. Matching is exact (case- and whitespace-sensitive), mirroring
    how ``prop()`` resolves names at evaluation time.

    The original quote character and the rest of the source are preserved;
    *new_name* is re-encoded for that quote. A malformed expression that no
    longer lexes is returned unchanged, so a property rename can never fail
    because of a pre-existing broken formula.
    """
    if old_name == new_name or not expression:
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
            and name_tok.value == "prop"
            and lparen_tok.type is TT.LPAREN
            and str_tok.type is TT.STRING
            and str_tok.value == old_name
        ):
            start = str_tok.pos
            end = _string_literal_end(expression, start)
            quote = expression[start]
            spans.append((start, end, _encode_string_literal(new_name, quote)))

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
