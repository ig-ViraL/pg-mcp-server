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


def test_must_start_with_select():
    ok, reason = validate_query("SHOW TABLES")
    assert ok is False

    ok, reason = validate_query("WITH cte AS (SELECT 1) SELECT * FROM cte")
    assert ok is False


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
