import pytest
from src.safety.validator import validate_query, validate_table_name, inject_limit


@pytest.mark.parametrize("sql", [
    "SELECT * FROM users",
    "SELECT id, name FROM orders WHERE id = 1",
    "SELECT COUNT(*) FROM products",
    "  select id from events  ",
    "SELECT a.id FROM accounts a JOIN users u ON a.user_id = u.id",
])
def test_valid_queries(sql):
    ok, reason = validate_query(sql)
    assert ok is True
    assert reason is None


@pytest.mark.parametrize("sql,keyword", [
    ("DROP TABLE users", "DROP"),
    ("DELETE FROM users WHERE id=1", "DELETE"),
    ("UPDATE users SET name='x'", "UPDATE"),
    ("INSERT INTO users VALUES (1,'x')", "INSERT"),
    ("TRUNCATE TABLE users", "TRUNCATE"),
    ("ALTER TABLE users ADD COLUMN foo TEXT", "ALTER"),
    ("CREATE TABLE foo (id INT)", "CREATE"),
    ("GRANT SELECT ON users TO public", "GRANT"),
    ("REVOKE SELECT ON users FROM public", "REVOKE"),
    ("EXEC sp_help", "EXEC"),
    ("EXECUTE sp_help", "EXECUTE"),
    ("COPY users TO '/tmp/out.csv'", "COPY"),
])
def test_blocked_keywords(sql, keyword):
    ok, reason = validate_query(sql)
    assert ok is False
    assert reason is not None


def test_must_start_with_select_or_with():
    ok, reason = validate_query("SHOW TABLES")
    assert ok is False


def test_read_only_cte_allowed():
    ok, reason = validate_query("WITH cte AS (SELECT 1) SELECT * FROM cte")
    assert ok is True


@pytest.mark.parametrize("sql", [
    "SELECT * INTO stolen FROM users",
    "SELECT * FROM users INTO OUTFILE '/tmp/x'",
    "WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d",
    "WITH m AS (MERGE INTO t USING s ON true WHEN MATCHED THEN DO NOTHING) SELECT 1",
    "SELECT * FROM users FOR SHARE",
    "SELECT * FROM users FOR NO KEY UPDATE",
    "SELECT /* hide */ 1",
    "SELECT * FROM users # mysql comment",
])
def test_writes_locks_and_comments_blocked(sql):
    ok, reason = validate_query(sql)
    assert ok is False
    assert reason is not None


@pytest.mark.parametrize("sql", [
    "SELECT pg_terminate_backend(123)",
    "SELECT pg_cancel_backend (123)",
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT setval('s', 1)",
    "SELECT nextval('s')",
    "SELECT set_config('role', 'admin', false)",
    "SELECT lo_unlink(1)",
    "SELECT pg_advisory_lock(1)",
    "SELECT dblink('host=x', 'select 1')",
    "SELECT SLEEP(5)",
    "SELECT LOAD_FILE('/etc/passwd')",
])
def test_server_side_functions_blocked(sql):
    ok, reason = validate_query(sql)
    assert ok is False
    assert "blocked function" in reason


@pytest.mark.parametrize("sql", [
    "SELECT created_at, updated_at, deleted_at FROM users",
    "SELECT data #> '{a,b}' AS x, data #>> '{a}' AS y FROM events",
    "SELECT * FROM settings WHERE intoxicated = false",
    "SELECT id FROM orders ORDER BY id LIMIT 5",
])
def test_harmless_lookalikes_allowed(sql):
    ok, reason = validate_query(sql)
    assert ok is True, reason


def test_inline_comment_blocked():
    ok, reason = validate_query("SELECT * FROM users -- bypass")
    assert ok is False


def test_multi_statement_blocked():
    ok, reason = validate_query("SELECT 1; DROP TABLE users")
    assert ok is False


def test_inject_limit_added():
    sql = "SELECT * FROM users"
    result = inject_limit(sql, 50)
    assert "LIMIT 50" in result


def test_inject_limit_not_duplicated():
    sql = "SELECT * FROM users LIMIT 10"
    result = inject_limit(sql, 50)
    assert result.count("LIMIT") == 1
    assert "LIMIT 10" in result


def test_validate_table_name_valid():
    for name in ["users", "order_items", "_temp", "Table123"]:
        ok, _ = validate_table_name(name)
        assert ok is True


def test_validate_table_name_invalid():
    for name in ["1users", "drop table", "users; DROP", "", "users--"]:
        ok, _ = validate_table_name(name)
        assert ok is False


def test_inject_limit_ignores_subquery_limit():
    sql = "SELECT * FROM (SELECT * FROM users LIMIT 5) u"
    result = inject_limit(sql, 50)
    assert result.endswith("LIMIT 50")


def test_inject_limit_caps_large_limit():
    assert inject_limit("SELECT * FROM users LIMIT 100000", 50) == "SELECT * FROM users LIMIT 50"
    assert inject_limit("SELECT * FROM users LIMIT 100000 OFFSET 10;", 50) == "SELECT * FROM users LIMIT 50 OFFSET 10"


def test_inject_limit_strips_trailing_semicolon():
    assert inject_limit("SELECT 1;", 50) == "SELECT 1 LIMIT 50"
