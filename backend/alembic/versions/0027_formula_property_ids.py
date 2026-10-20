"""store formula property references as property ids

Revision ID: a7d9c8y00027
Revises: z6c8b7x90026
Create Date: 2026-10-20

A formula names the properties it reads with ``prop('…')``. These references
were stored as property names, so renaming a property broke every formula that
read it unless the rename rewrote them, and a formula moved to another
database kept pointing at whatever carried the same name there.

Formulas are now stored with property ids and translated to names only where
the user sees or edits them. This revision rewrites the stored expression of
every formula: each ``prop()`` whose string argument is the name of a property
of the same database becomes ``prop('<id>')``. Arguments that name no property
of the database, and arguments that are computed rather than a literal, stay
as they are; the application binds such a name once a property with it exists.
An expression that does not scan is left untouched.

The scanner is copied here rather than imported, so the revision keeps
producing the same result however the formula engine changes later. It
follows the engine's lexer for the parts that matter: string literals with
backslash escapes, numbers, identifiers and single-character punctuation.

Downgrade
---------
Turns the ids back into the names of the properties they refer to, the form
the previous code reads.
"""
import json
import logging
import re
from typing import Any, Mapping, Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7d9c8y00027"
down_revision: Union[str, None] = "z6c8b7x90026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


# ─── Scanning (pure, no database access) ──────────────────────────────────────

_NUMBER_RE = re.compile(r"\d+(\.\d+)?")
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}
_TWO_CHAR = ("==", "!=", "<=", ">=")
_ONE_CHAR = frozenset("+-*/%^<>(),")


def _tokens(src: str) -> Optional[list[tuple[str, Any, int, int]]]:
    """
    ``(kind, value, start, end)`` for each token of *src*, kind being
    ``string``, ``ident``, ``number`` or ``punct``; None when *src* does not
    scan.
    """
    tokens: list[tuple[str, Any, int, int]] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c in ("'", '"'):
            j = i + 1
            buf: list[str] = []
            while j < n and src[j] != c:
                if src[j] == "\\" and j + 1 < n:
                    buf.append(_ESCAPES.get(src[j + 1], src[j + 1]))
                    j += 2
                else:
                    buf.append(src[j])
                    j += 1
            if j >= n:
                return None
            tokens.append(("string", "".join(buf), i, j + 1))
            i = j + 1
            continue
        m = _NUMBER_RE.match(src, i)
        if m:
            tokens.append(("number", m.group(), i, m.end()))
            i = m.end()
            continue
        m = _IDENT_RE.match(src, i)
        if m:
            tokens.append(("ident", m.group(), i, m.end()))
            i = m.end()
            continue
        if src[i:i + 2] in _TWO_CHAR:
            tokens.append(("punct", src[i:i + 2], i, i + 2))
            i += 2
            continue
        if c in _ONE_CHAR:
            tokens.append(("punct", c, i, i + 1))
            i += 1
            continue
        return None
    return tokens


def _encode(value: str, quote: str) -> str:
    """*value* as a string literal in *quote* that scans back to *value*."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace(quote, "\\" + quote)
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f"{quote}{escaped}{quote}"


def map_references(expression: str, mapping: Mapping[str, str]) -> str:
    """
    *expression* with the literal argument of every ``prop(…)`` call that is
    a key of *mapping* replaced by its value, in the same quote. Everything
    else, formatting included, stays as it is.
    """
    if not expression or not mapping:
        return expression
    tokens = _tokens(expression)
    if tokens is None:
        return expression
    spans: list[tuple[int, int, str]] = []
    for name, paren, arg in zip(tokens, tokens[1:], tokens[2:]):
        if (
            name[0] == "ident" and name[1].lower() == "prop"
            and paren[0] == "punct" and paren[1] == "("
            and arg[0] == "string"
            and arg[1] in mapping
            and mapping[arg[1]] != arg[1]
        ):
            quote = expression[arg[2]]
            spans.append((arg[2], arg[3], _encode(mapping[arg[1]], quote)))
    out = expression
    for start, end, literal in reversed(spans):
        out = out[:start] + literal + out[end:]
    return out


def to_ids(expression: str, properties: Sequence[tuple[str, str]]) -> str:
    """
    *expression* with references to the names in *properties* (``(id,
    name)`` pairs of one database) as ids. A name spelled like one of the
    ids is read as that id and left alone.
    """
    ids = {pid for pid, _ in properties}
    return map_references(
        expression, {name: pid for pid, name in properties if name not in ids},
    )


def to_names(expression: str, properties: Sequence[tuple[str, str]]) -> str:
    """*expression* with references to the ids in *properties* as names."""
    return map_references(expression, {pid: name for pid, name in properties})


# ─── Database access ──────────────────────────────────────────────────────────


def _load(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _rewrite(connection: sa.engine.Connection, translate) -> int:
    """
    Apply *translate(expression, properties)* to every formula, with the
    properties of its own database; returns how many formulas changed.
    """
    properties: dict[Any, list[tuple[str, str]]] = {}
    for row in connection.execute(sa.text(
        "SELECT id, database_id, name FROM property_schemas"
    )):
        properties.setdefault(row.database_id, []).append((str(row.id), row.name))

    formulas = connection.execute(sa.text(
        "SELECT id, database_id, config FROM property_schemas "
        "WHERE type = 'formula' AND config IS NOT NULL"
    )).all()
    update = sa.text(
        "UPDATE property_schemas SET config = CAST(:config AS jsonb) WHERE id = :id"
    )
    changed = 0
    for row in formulas:
        config = _load(row.config)
        if not isinstance(config, dict):
            continue
        expression = config.get("expression")
        if not isinstance(expression, str) or not expression:
            continue
        rewritten = translate(expression, properties.get(row.database_id, []))
        if rewritten != expression:
            connection.execute(
                update, {"id": row.id, "config": json.dumps({**config, "expression": rewritten})},
            )
            changed += 1
    return changed


def migrate(connection: sa.engine.Connection) -> int:
    """Store every formula's references as ids; returns how many changed."""
    return _rewrite(connection, to_ids)


def revert(connection: sa.engine.Connection) -> int:
    """Turn every formula's id references back into names."""
    return _rewrite(connection, to_names)


def upgrade() -> None:
    changed = migrate(op.get_bind())
    if changed:
        log.info("formula reference migration bound %d formula(s) to property ids", changed)


def downgrade() -> None:
    changed = revert(op.get_bind())
    if changed:
        log.info("formula reference migration turned %d formula(s) back to names", changed)
