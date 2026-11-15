"""Integration tests against a real PostgreSQL.

Run with:  PGMCP_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/mcptest pytest
Skipped when the variable is not set. The tests create and drop their own tables.
"""

import json
import os

import asyncpg
import pytest

from pg_mcp_server.config import Policy, get_policy, set_policy
from pg_mcp_server.db.drivers.postgres import PostgresDriver

URL = os.environ.get("PGMCP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="PGMCP_TEST_DATABASE_URL not set")

SETUP = """
DROP TABLE IF EXISTS pgmcp_orders, pgmcp_customers, pgmcp_canary CASCADE;
DROP FUNCTION IF EXISTS pgmcp_sneaky_write();
CREATE TABLE pgmcp_customers (id serial PRIMARY KEY, name text, email text, phone text);
CREATE TABLE pgmcp_orders (id serial PRIMARY KEY, customer_id int REFERENCES pgmcp_customers(id),
                           total numeric(10,2), created_at timestamptz DEFAULT now());
CREATE TABLE pgmcp_canary (id int);
INSERT INTO pgmcp_customers (name, email, phone)
  SELECT 'Customer ' || g, 'c' || g || '@example.com', '+1555' || g FROM generate_series(1, 20) g;
INSERT INTO pgmcp_orders (customer_id, total)
  SELECT 1 + (g % 20), g * 1.5 FROM generate_series(1, 1000) g;
CREATE FUNCTION pgmcp_sneaky_write() RETURNS int LANGUAGE sql AS
  'INSERT INTO pgmcp_canary VALUES (1); SELECT 1';
"""


@pytest.fixture
async def driver(tmp_path):
    conn = await asyncpg.connect(URL)
    await conn.execute(SETUP)
    await conn.close()
    old = get_policy()
    set_policy(Policy(
        max_rows=50,
        statement_timeout_ms=1000,
        deny_columns=frozenset({("pgmcp_customers", "email"), ("pgmcp_customers", "phone")}),
        audit_path=str(tmp_path / "audit.jsonl"),
    ))
    d = PostgresDriver()
    await d.connect(URL)
    yield d
    await d.close()
    set_policy(old)
    conn = await asyncpg.connect(URL)
    await conn.execute("DROP TABLE IF EXISTS pgmcp_orders, pgmcp_customers, pgmcp_canary CASCADE; "
                       "DROP FUNCTION IF EXISTS pgmcp_sneaky_write();")
    await conn.close()


def _audit():
    with open(get_policy().audit_path) as fh:
        return [json.loads(line) for line in fh]


async def test_select_and_row_cap(driver):
    r = await driver.run_query("SELECT id, total FROM pgmcp_orders ORDER BY id", limit=1000)
    assert not r["isError"], r["content"]
    data = json.loads(r["content"])
    assert data["row_count"] == 50 and data["truncated"] is True
    assert data["rows"][0]["id"] == 1
    event = _audit()[-1]
    assert event["decision"] == "allowed" and event["rows"] == 50 and event["truncated"]


async def test_existing_limit_respected(driver):
    r = await driver.run_query("SELECT id FROM pgmcp_orders LIMIT 3")
    data = json.loads(r["content"])
    assert data["row_count"] == 3 and "truncated" not in data


async def test_stacked_statement_blocked_and_logged(driver):
    r = await driver.run_query("SELECT 1; DROP TABLE pgmcp_orders")
    assert r["isError"] and "one statement" in r["content"]
    assert _audit()[-1]["decision"] == "blocked"
    r = await driver.run_query("SELECT count(*) AS n FROM pgmcp_orders")
    assert json.loads(r["content"])["rows"][0]["n"] == 1000


async def test_writable_cte_blocked(driver):
    r = await driver.run_query("WITH d AS (DELETE FROM pgmcp_orders RETURNING *) SELECT count(*) FROM d")
    assert r["isError"]


async def test_read_only_transaction_is_the_backstop(driver):
    # A user-defined function that writes passes the parser, but the READ ONLY
    # transaction stops it inside the database.
    r = await driver.run_query("SELECT pgmcp_sneaky_write()")
    assert r["isError"] and "25006" in r["content"]  # read_only_sql_transaction
    conn = await asyncpg.connect(URL)
    assert await conn.fetchval("SELECT count(*) FROM pgmcp_canary") == 0
    await conn.close()


async def test_statement_timeout(driver):
    r = await driver.run_query(
        "SELECT count(*) FROM generate_series(1, 100000000) a"
    )
    assert r["isError"] and "timed out" in r["content"]
    assert _audit()[-1]["decision"] == "error"


async def test_restricted_columns(driver):
    r = await driver.run_query("SELECT email FROM pgmcp_customers")
    assert r["isError"] and "restricted" in r["content"]
    r = await driver.run_query("SELECT * FROM pgmcp_customers")
    assert r["isError"]
    r = await driver.run_query("SELECT id, name FROM pgmcp_customers ORDER BY id LIMIT 1")
    assert not r["isError"]

    d = json.loads((await driver.describe_table("pgmcp_customers"))["content"])
    assert all("email" not in row and "phone" not in row for row in d["sample_rows"])
    assert {c["column"] for c in d["columns"] if c.get("restricted")} == {"email", "phone"}

    t = json.loads((await driver.list_tables())["content"])
    cols = {c["column"]: c for c in t["pgmcp_customers"]["columns"]}
    assert cols["email"].get("restricted") is True


async def test_table_policy(driver):
    set_policy(Policy(allow_tables=frozenset({"pgmcp_orders"}), audit_path=get_policy().audit_path))
    assert (await driver.run_query("SELECT * FROM pgmcp_customers"))["isError"]
    assert (await driver.get_row_count("pgmcp_customers"))["isError"]
    assert (await driver.describe_table("pgmcp_customers"))["isError"]
    tables = json.loads((await driver.list_tables())["content"])
    assert "pgmcp_customers" not in tables and "pgmcp_orders" in tables
    assert json.loads((await driver.get_row_count("pgmcp_orders"))["content"])["row_count"] == 1000


async def test_explain(driver):
    r = await driver.explain_query("SELECT * FROM pgmcp_orders WHERE customer_id = 3")
    assert not r["isError"]
    assert "Plan" in json.loads(r["content"])[0]
    r = await driver.explain_query("SELECT * FROM pgmcp_orders", analyze=True)
    assert r["isError"] and "disabled" in r["content"]
    r = await driver.explain_query("DELETE FROM pgmcp_orders")
    assert r["isError"]


async def test_annotations_can_be_disabled(driver):
    r = await driver.annotate_column("pgmcp_orders", "total", "Order total in USD")
    assert not r["isError"], r["content"]
    t = json.loads((await driver.list_tables())["content"])
    total = next(c for c in t["pgmcp_orders"]["columns"] if c["column"] == "total")
    assert total["comment"] == "Order total in USD"

    set_policy(Policy(allow_annotations=False))
    r = await driver.annotate_table("pgmcp_orders", "x")
    assert r["isError"] and "disabled" in r["content"]
