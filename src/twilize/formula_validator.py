"""Validate Tableau calculation formulas against the official function catalog.

Motivation
----------
0.33.1 shipped KPI calcs that used ``CHR(10)`` for a newline. Tableau has no
``CHR`` function (it's spelled ``CHAR``), so every KPI field opened in Tableau
with "Unknown function CHR called." and the calc was flagged invalid. A single
typo in a formula string silently produced a broken workbook — there was no
guard rail.

This module loads the 151-function catalog shipped at
``src/twilize/references/tableau_all_functions.json`` and exposes:

    validate_formula(formula) -> list[str]
        Returns a list of unknown identifiers used as function calls. Empty
        list means the formula passes.

    assert_valid_formula(formula, field_name="") -> None
        Raises ValueError with a clear message (including the closest-match
        suggestion) if any unknown function is called. No-op otherwise.

We deliberately scan for *function calls only* (an identifier immediately
followed by ``(``), not bare tokens — that way keywords like ``IF`` / ``THEN``
/ ``ELSE`` / ``END`` / ``AND`` / ``OR`` / ``NOT`` and field references in
``[Brackets]`` don't produce false positives.
"""

from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

_CATALOG_PATH = Path(__file__).parent / "references" / "tableau_all_functions.json"

# Tableau keywords — these may appear before a '(' in things like
# `IF (cond) THEN ...` and must NOT be flagged as unknown function calls.
_KEYWORDS: frozenset[str] = frozenset({
    "IF", "THEN", "ELSE", "ELSEIF", "END", "CASE", "WHEN",
    "AND", "OR", "NOT", "IN", "TRUE", "FALSE", "NULL",
})

# Identifier immediately followed by '(' — with optional whitespace.
# We capture the name only. String literals are stripped first so a
# literal like `'CHR('` can't trigger a false positive.
_FUNC_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# Tableau string literals use single quotes. Escaped quote is '' (doubled).
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

# Bracketed field / calculation references: [Field], [Calculation_XYZ],
# [datasource].[field]. Stripped so tokens inside brackets never collide.
_BRACKET_REF_RE = re.compile(r"\[[^\]]*\]")


@lru_cache(maxsize=1)
def _valid_function_names() -> frozenset[str]:
    """Load the canonical set of Tableau function names (upper-cased).

    Cached per-process — the JSON is immutable at runtime.
    """
    with _CATALOG_PATH.open(encoding="utf-8") as fh:
        entries = json.load(fh)
    names = set()
    for e in entries:
        if isinstance(e, dict) and "name" in e:
            names.add(e["name"].upper())
        elif isinstance(e, str):
            names.add(e.upper())
    return frozenset(names)


def _strip_non_code(formula: str) -> str:
    """Remove string literals and bracketed references so the regex scan
    only sees actual calculation code."""
    no_strings = _STRING_LITERAL_RE.sub("''", formula)
    no_brackets = _BRACKET_REF_RE.sub("[]", no_strings)
    return no_brackets


def validate_formula(formula: str) -> list[str]:
    """Return the list of unknown function names called in ``formula``.

    An empty list means every function call targets a valid Tableau function.
    Case-insensitive.
    """
    if not formula:
        return []
    valid = _valid_function_names()
    code = _strip_non_code(formula)
    unknown: list[str] = []
    seen: set[str] = set()
    for match in _FUNC_CALL_RE.finditer(code):
        name = match.group(1).upper()
        if name in _KEYWORDS or name in valid or name in seen:
            continue
        seen.add(name)
        unknown.append(name)
    return unknown


def _suggest(name: str) -> str | None:
    """Return the closest Tableau function name to ``name`` (or None)."""
    matches = difflib.get_close_matches(
        name.upper(), list(_valid_function_names()), n=1, cutoff=0.6
    )
    return matches[0] if matches else None


def assert_valid_formula(formula: str, field_name: str = "") -> None:
    """Raise ValueError if the formula calls any non-Tableau function.

    The error message names every offender and suggests a close match when
    one exists — so ``CHR`` → "Did you mean CHAR?".
    """
    unknown = validate_formula(formula)
    if not unknown:
        return
    parts = []
    for name in unknown:
        sugg = _suggest(name)
        parts.append(f"{name}" + (f" (did you mean {sugg}?)" if sugg else ""))
    prefix = f"Calculated field '{field_name}': " if field_name else ""
    raise ValueError(
        f"{prefix}formula calls unknown Tableau function(s): {', '.join(parts)}. "
        f"See src/twilize/references/tableau_all_functions.json for the valid set."
    )
