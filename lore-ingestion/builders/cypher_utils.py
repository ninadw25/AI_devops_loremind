"""
builders/cypher_utils.py
─────────────────────────
Shared Cypher emission helpers used by builders/unified_builder.py and
builders/post_processor.py.

Single owner for Cypher string escaping. Both builders previously defined
their own `_esc()` with subtly different semantics — `unified_builder`
stripped semicolons (which split Cypher batches), `post_processor` did not.
The version here matches the stricter `unified_builder` behavior, so any
`;` inside a literal is replaced with `,` and cannot break batch loading.
"""


def cypher_escape(s) -> str:
    """Escape a value for use inside a Cypher single-quoted string literal.

    Replaces in order:
      - backslash → double-backslash (must come first)
      - single quote → escaped single quote
      - semicolon → comma (semicolons split Cypher batch files)
      - newline / carriage return → space
    """
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(";", ",")
        .replace("\n", " ")
        .replace("\r", "")
    )
