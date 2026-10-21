"""Static checks on model-written SQL.

This is the first layer only. Drivers also run every query in a read-only
transaction with a statement timeout, so a query that slips past these
checks still cannot write.
"""
import re

# Only SELECT/WITH can start a query and stacked statements are refused, so this
# list covers what can still appear inside one: data-modifying CTEs, SELECT INTO,
# row locks, comments that could cut off the injected LIMIT, and stacking.
BLOCKED = [
    r'\bDROP\b', r'\bDELETE\b', r'\bUPDATE\b', r'\bINSERT\b',
    r'\bTRUNCATE\b', r'\bALTER\b', r'\bCREATE\b', r'\bGRANT\b',
    r'\bREVOKE\b', r'\bEXEC\b', r'\bEXECUTE\b', r'\bCOPY\b',
    r'\bMERGE\b',
    # SELECT ... INTO creates a table (Postgres) or writes a file (MySQL OUTFILE)
    r'\bINTO\b',
    # Row locks (FOR UPDATE is already caught by UPDATE)
    r'\bFOR\s+(?:SHARE|NO\s+KEY|KEY)\b',
    # Comments: SQL, C-style and MySQL '#' (but not Postgres JSON operators #> and #>>)
    r'--', r'/\*', r'#(?!>)',
    # Stacked statements
    r';.+\S',
]

# Functions that act on the server rather than read data.
BLOCKED_FUNCTIONS = re.compile(
    r'\b('
    r'pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile'
    r'|pg_sleep\w*|pg_read_file|pg_read_binary_file|pg_ls_\w+|pg_stat_file'
    r'|pg_advisory\w*|pg_try_advisory\w*|pg_create_\w+|pg_drop_replication_slot'
    r'|pg_switch_wal|pg_promote|pg_logical_emit_message'
    r'|lo_\w+|dblink\w*|set_config|setval|nextval|txid_current\w*'
    r'|sleep|benchmark|load_file|get_lock|release_lock|load_extension'
    r'|writefile|readfile'
    r')\s*\(',
    re.IGNORECASE,
)

TABLE_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
_TRAILING_LIMIT_RE = re.compile(r'\bLIMIT\s+(\d+)(\s+OFFSET\s+\d+)?$', re.IGNORECASE)


def validate_query(sql: str) -> tuple[bool, str | None]:
    """Returns (ok, reason). reason is None when ok=True."""
    stripped = sql.strip()
    first = stripped.split(None, 1)[0].upper() if stripped else ''
    if first not in ('SELECT', 'WITH'):
        return False, "Query must start with SELECT or WITH"
    for pattern in BLOCKED:
        if re.search(pattern, stripped, re.IGNORECASE):
            return False, f"Query contains blocked pattern: {pattern}"
    match = BLOCKED_FUNCTIONS.search(stripped)
    if match:
        return False, f"Query calls a blocked function: {match.group(1)}"
    return True, None


def validate_table_name(name: str) -> tuple[bool, str | None]:
    if not TABLE_NAME_RE.match(name):
        return False, f"Invalid table name: {name!r}"
    return True, None


def inject_limit(sql: str, limit: int) -> str:
    """Make sure the outer query returns at most `limit` rows.

    Only a trailing LIMIT counts (one inside a subquery does not), and a
    trailing LIMIT larger than `limit` is lowered to `limit`.
    """
    body = sql.strip().rstrip(';').rstrip()
    match = _TRAILING_LIMIT_RE.search(body)
    if not match:
        return f"{body} LIMIT {limit}"
    if int(match.group(1)) > limit:
        return f"{body[:match.start()]}LIMIT {limit}{match.group(2) or ''}"
    return body
