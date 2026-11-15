import re

BLOCKED = [
    r'\bDROP\b', r'\bDELETE\b', r'\bUPDATE\b', r'\bINSERT\b',
    r'\bTRUNCATE\b', r'\bALTER\b', r'\bCREATE\b', r'\bGRANT\b',
    r'\bREVOKE\b', r'\bEXEC\b', r'\bEXECUTE\b', r'\bCOPY\b',
    r'--',
    r';.+\S',
]

TABLE_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def validate_query(sql: str) -> tuple[bool, str | None]:
    """Returns (ok, reason). reason is None when ok=True."""
    stripped = sql.strip()
    if not stripped.upper().startswith('SELECT'):
        return False, "Query must start with SELECT"
    for pattern in BLOCKED:
        if re.search(pattern, stripped, re.IGNORECASE):
            return False, f"Query contains blocked pattern: {pattern}"
    return True, None


def validate_table_name(name: str) -> tuple[bool, str | None]:
    if not TABLE_NAME_RE.match(name):
        return False, f"Invalid table name: {name!r}"
    return True, None


def inject_limit(sql: str, limit: int) -> str:
    """Append LIMIT if the query doesn't already have one."""
    if not re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
        return f"{sql.rstrip().rstrip(';')} LIMIT {limit}"
    return sql
