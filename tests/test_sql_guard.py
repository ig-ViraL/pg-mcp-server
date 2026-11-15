import pytest

from pg_mcp_server.config import Policy, load_policy
from pg_mcp_server.safety.sql_guard import check_sql

OPEN = Policy()


@pytest.mark.parametrize("sql", [
    "SELECT * FROM users",
    "select id, name from orders where id = 1",
    "SELECT COUNT(*) FROM products;",
    "WITH recent AS (SELECT * FROM orders WHERE created_at > now() - interval '7 days') SELECT count(*) FROM recent",
    "SELECT a.id FROM accounts a JOIN users u ON a.user_id = u.id",
    "SELECT * FROM users -- trailing comment is fine, the parser drops it",
    "/* leading comment */ SELECT 1",
    "VALUES (1, 'a'), (2, 'b')",
    "SELECT 'DROP TABLE users; DELETE FROM x' AS harmless_string",
    "SELECT date_trunc('month', created_at), sum(total) FROM orders GROUP BY 1 ORDER BY 1",
    "SELECT * FROM (SELECT id FROM users) sub",
    "SELECT id FROM users UNION ALL SELECT id FROM admins_archive",
    "SELECT row_number() OVER (PARTITION BY customer_id ORDER BY created_at) FROM orders",
])
def test_allowed(sql):
    r = check_sql(sql, OPEN)
    assert r.ok, r.reason


@pytest.mark.parametrize("sql,needle", [
    ("DROP TABLE users", "Only SELECT"),
    ("DELETE FROM users", "Only SELECT"),
    ("UPDATE users SET name = 'x'", "Only SELECT"),
    ("INSERT INTO users VALUES (1)", "Only SELECT"),
    ("TRUNCATE users", "Only SELECT"),
    ("COPY users TO '/tmp/x'", "Only SELECT"),
    ("SET statement_timeout = 0", "Only SELECT"),
    ("BEGIN", "Only SELECT"),
    ("SHOW ALL", "Only SELECT"),
    ("CALL do_something()", "Only SELECT"),
    ("DO $$ BEGIN PERFORM 1; END $$", "Only SELECT"),
    ("EXPLAIN ANALYZE DELETE FROM users", "Only SELECT"),
    # stacked statements, the classic read-only-transaction bypass
    ("SELECT 1; DROP TABLE users", "Exactly one statement"),
    ("SELECT 1; COMMIT; DROP TABLE users", "Exactly one statement"),
    # data-modifying CTEs
    ("WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d", "DeleteStmt"),
    ("WITH u AS (UPDATE users SET admin = true RETURNING id) SELECT 1", "UpdateStmt"),
    ("WITH i AS (INSERT INTO t VALUES (1) RETURNING 1) SELECT * FROM i", "InsertStmt"),
    # SELECT INTO creates a table
    ("SELECT * INTO new_table FROM users", "SELECT INTO"),
    # locks
    ("SELECT * FROM users FOR UPDATE", "locking"),
    ("SELECT * FROM users FOR SHARE NOWAIT", "locking"),
    # dangerous functions, with schema qualification and case tricks
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT pg_catalog.PG_READ_FILE('/etc/passwd')", "pg_read_file"),
    ('SELECT "pg_read_file"(\'/etc/passwd\')', "pg_read_file"),
    ("SELECT lo_import('/etc/passwd')", "lo_import"),
    ("SELECT lo_export(1234, '/tmp/x')", "lo_export"),
    ("SELECT * FROM dblink('host=evil', 'select 1') AS t(a int)", "dblink"),
    ("SELECT pg_sleep(100)", "pg_sleep"),
    ("SELECT pg_terminate_backend(pid) FROM pg_stat_activity", "pg_terminate_backend"),
    ("SELECT set_config('statement_timeout', '0', false)", "set_config"),
    ("SELECT nextval('users_id_seq')", "nextval"),
    ("SELECT query_to_xml('select * from pg_authid', true, true, '')", "query_to_xml"),
    ("SELECT pg_advisory_lock(1)", "pg_advisory"),
    ("SELECT id FROM users WHERE id = (SELECT pg_read_file('x')::int)", "pg_read_file"),
    # credential catalogs
    ("SELECT rolname, rolpassword FROM pg_authid", "pg_authid"),
    ("SELECT * FROM pg_catalog.pg_shadow", "pg_shadow"),
    # junk
    ("", "empty"),
    ("SELEC * FROM users", "parse"),
])
def test_blocked(sql, needle):
    r = check_sql(sql, OPEN)
    assert not r.ok
    assert needle.lower() in r.reason.lower(), r.reason


def test_allowlist():
    pol = Policy(allow_tables=frozenset({"orders", "customers"}))
    assert check_sql("SELECT * FROM orders o JOIN customers c ON c.id = o.customer_id", pol).ok
    r = check_sql("SELECT * FROM orders JOIN secrets ON true", pol)
    assert not r.ok and "secrets" in r.reason


def test_allowlist_ignores_cte_names():
    pol = Policy(allow_tables=frozenset({"orders"}))
    assert check_sql("WITH recent AS (SELECT * FROM orders) SELECT * FROM recent", pol).ok


def test_denylist_with_schema():
    pol = Policy(deny_tables=frozenset({"admin_users"}))
    assert not check_sql("SELECT * FROM public.admin_users", pol).ok
    assert not check_sql("SELECT * FROM users WHERE id IN (SELECT id FROM admin_users)", pol).ok


class TestDeniedColumns:
    pol = Policy(deny_columns=frozenset({("customers", "email"), ("customers", "phone")}))

    def test_explicit_columns_ok(self):
        assert check_sql("SELECT id, name FROM customers", self.pol).ok
        assert check_sql("SELECT c.id FROM customers c", self.pol).ok

    def test_restricted_column_blocked(self):
        assert not check_sql("SELECT email FROM customers", self.pol).ok
        assert not check_sql("SELECT c.email FROM customers c", self.pol).ok
        assert not check_sql("SELECT id FROM customers WHERE phone LIKE '+1%'", self.pol).ok

    def test_star_blocked(self):
        r = check_sql("SELECT * FROM customers", self.pol)
        assert not r.ok and "SELECT *" in r.reason
        assert not check_sql("SELECT c.* FROM customers c", self.pol).ok

    def test_star_on_other_table_ok(self):
        assert check_sql("SELECT o.*, c.name FROM orders o JOIN customers c ON c.id = o.customer_id", self.pol).ok

    def test_whole_row_reference_blocked(self):
        assert not check_sql("SELECT row_to_json(c) FROM customers c", self.pol).ok

    def test_other_tables_unaffected(self):
        assert check_sql("SELECT * FROM orders", self.pol).ok
        assert check_sql("SELECT email FROM newsletter_signups", self.pol).ok


def test_fingerprint_ignores_literals():
    a = check_sql("SELECT * FROM users WHERE id = 1", OPEN)
    b = check_sql("SELECT * FROM users WHERE id = 42", OPEN)
    assert a.fingerprint and a.fingerprint == b.fingerprint


def test_tables_reported():
    r = check_sql("SELECT * FROM orders o JOIN public.customers c ON true", OPEN)
    assert r.tables == ["orders", "customers"]


def test_load_policy_env_and_toml(tmp_path):
    cfg = tmp_path / "pg-mcp.toml"
    cfg.write_text(
        '[policy]\nallow_tables = ["orders"]\ndeny_columns = ["public.customers.email"]\nmax_rows = 50\n'
        '[audit]\npath = "audit.jsonl"\n'
    )
    pol = load_policy(str(cfg), env={})
    assert pol.allow_tables == {"orders"}
    assert pol.deny_columns == {("customers", "email")}
    assert pol.max_rows == 50 and pol.default_rows == 50
    assert pol.audit_path == "audit.jsonl"
    assert pol.clamp_rows(1000) == 50

    pol = load_policy(str(cfg), env={"PGMCP_ALLOW_TABLES": "a,b", "PGMCP_MAX_ROWS": "10"})
    assert pol.allow_tables == {"a", "b"} and pol.max_rows == 10


def test_bad_deny_column_rejected():
    with pytest.raises(ValueError):
        load_policy(None, env={"PGMCP_DENY_COLUMNS": "email"})
