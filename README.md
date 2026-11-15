<div align="center">

# 🐘 pg-mcp-server

**The safe way to let AI agents query your Postgres.**

Any PostgreSQL, read-only by construction, every query audited, sensitive columns kept out of reach, and it learns your business vocabulary.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white) ![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20Streamable%20HTTP-d77757) ![Tests](https://github.com/ig-ViraL/pg-mcp-server/actions/workflows/ci.yml/badge.svg) ![License](https://img.shields.io/badge/License-MIT-green)

[Live demo](https://www.viral-deshle.cloud/pg-mcp) · [Quick start](#quick-start) · [Safety model](#safety-model) · [Configuration](#configuration)

</div>

## Why this one

Most Postgres MCP servers block writes with a keyword list or a read-only transaction flag. Both have been bypassed in public (stacked statements, writable CTEs, file-reading functions). pg-mcp-server checks every query with PostgreSQL's own parser and then runs it inside a read-only transaction, so one layer failing doesn't hand the agent your data.

| | |
|:--|:--|
| 🛡️ **Parser-based guard** | Every query is parsed with libpg_query. Only a single `SELECT` gets through. Writable CTEs, `SELECT INTO`, `FOR UPDATE`, and file, network and admin functions are refused. |
| 🔒 **Read-only transaction + timeout** | Allowed queries run in `READ ONLY` transactions with a statement timeout and a hard row cap. |
| 🙈 **Table and column policy** | Allowlist or denylist tables. Mark columns like `customers.email` as restricted and the agent can't read them, even through `SELECT *` or `row_to_json()`. |
| 📜 **Audit log** | One JSON line per call: tool, decision, query fingerprint, tables, row count, duration. Never result data, never passwords. |
| 🧠 **Better answers** | Schema with PKs, FKs, indexes and comments, `search_schema`, an `nl_to_sql` prompt, and `annotate_*` tools that write business meaning back into Postgres comments so every future query benefits. |
| 🩺 **DBA tools** | `EXPLAIN` plans, slow queries from `pg_stat_statements`, index and bloat health checks. |
| 🔐 **Bring your own DB over HTTP** | Remote callers send their connection string as an AES-256-GCM token in `X-DB-URL`. Nothing is stored. |

## Quick start

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uvx pg-mcp-server --database-url postgresql://readonly_user:password@localhost:5432/mydb
```

### Claude Desktop

Add to `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "postgres": {
      "command": "uvx",
      "args": ["pg-mcp-server"],
      "env": {
        "DATABASE_URL": "postgresql://readonly_user:password@localhost:5432/mydb",
        "PGMCP_AUDIT_LOG": "/Users/you/pg-mcp-audit.jsonl"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add postgres -e DATABASE_URL=postgresql://readonly_user:password@localhost:5432/mydb -- uvx pg-mcp-server
```

### Cursor

`.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "postgres": {
      "command": "uvx",
      "args": ["pg-mcp-server"],
      "env": { "DATABASE_URL": "postgresql://readonly_user:password@localhost:5432/mydb" }
    }
  }
}
```

### Remote server (Streamable HTTP)

```bash
pg-mcp-server --transport http --host 0.0.0.0 --port 8000
# MCP endpoint: http://your-host:8000/mcp
```

Or with Docker:

```bash
docker build -t pg-mcp-server .
docker run -p 8000:8000 -e DATABASE_URL=postgresql://... -e ALLOWED_HOSTS=your-host.example.com pg-mcp-server
```

## Tools

| Tool | What it does |
|:--|:--|
| `list_tables` | Tables with columns, types, PKs, FKs, indexes and comments (restricted columns are flagged) |
| `describe_table` | Deep metadata for one table, plus sample rows with restricted columns removed |
| `search_schema` | Keyword search across table, column and comment text |
| `get_row_count` | Exact row count for a table |
| `run_query` | One checked, read-only `SELECT` with row cap and timeout |
| `explain_query` | Execution plan as JSON (`EXPLAIN ANALYZE` is opt-in) |
| `get_slow_queries` | Slowest statements from `pg_stat_statements` |
| `get_db_health` | Unused indexes, table bloat, long-running queries, connection usage |
| `annotate_table` / `annotate_column` | Save business descriptions as Postgres comments (can be disabled) |

Plus the `nl_to_sql` prompt, which tells the model to inspect foreign keys before writing joins.

## Safety model

Each layer works on its own, so a mistake in one is caught by the next.

1. **Least-privilege role (you).** Connect as a role that can only `SELECT`. See below.
2. **SQL guard.** The query is parsed with the same parser PostgreSQL uses. It must be exactly one `SELECT` (including `WITH … SELECT` and `VALUES`). Refused anywhere in the tree:
   - `INSERT`, `UPDATE`, `DELETE`, `MERGE` and any other statement, including inside CTEs
   - `SELECT INTO`, `FOR UPDATE` / `FOR SHARE`
   - functions that read server files, open connections, signal backends, change settings, take advisory locks or advance sequences (`pg_read_file`, `lo_import`, `dblink`, `pg_terminate_backend`, `set_config`, `nextval`, `pg_sleep`, `query_to_xml`, …)
   - credential catalogs such as `pg_authid` and `pg_shadow`
   - tables and columns your policy denies
3. **Read-only transaction.** Allowed queries run in `BEGIN READ ONLY` with `SET LOCAL statement_timeout`. A user-defined function that tries to write still fails here.
4. **Row cap.** Results are fetched through a cursor and cut at `max_rows`; the response says when it was truncated.
5. **Audit log.** Allowed, blocked and failed calls are all recorded.

The column denylist works on the parsed query and covers `SELECT *`, `t.*`, qualified and bare column names, and whole-row references. For a hard guarantee on very sensitive data, also use column-level `GRANT`s in the database.

### Recommended read-only role

```sql
CREATE ROLE mcp_readonly LOGIN PASSWORD 'strong_password_here';
GRANT CONNECT ON DATABASE your_db TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_readonly;
-- Optional: let agents write business comments (annotate_* tools) on tables it owns,
-- or set PGMCP_ALLOW_ANNOTATIONS=false.
```

## Configuration

Settings come from a TOML file (`--config pg-mcp.toml` or `PGMCP_CONFIG`) and are overridden by environment variables. See [`pg-mcp.example.toml`](pg-mcp.example.toml).

| Setting | Env var | Default |
|:--|:--|:--|
| `policy.allow_tables` | `PGMCP_ALLOW_TABLES` (comma separated) | all tables |
| `policy.deny_tables` | `PGMCP_DENY_TABLES` | none |
| `policy.deny_columns` | `PGMCP_DENY_COLUMNS` (`table.column,…`) | none |
| `policy.max_rows` | `PGMCP_MAX_ROWS` | `500` |
| `policy.statement_timeout_ms` | `PGMCP_STATEMENT_TIMEOUT_MS` | `5000` |
| `policy.allow_annotations` | `PGMCP_ALLOW_ANNOTATIONS` | `true` |
| `policy.allow_explain_analyze` | `PGMCP_ALLOW_EXPLAIN_ANALYZE` | `false` |
| `audit.path` | `PGMCP_AUDIT_LOG` (`-` for stderr) | off |
| `audit.include_sql` | `PGMCP_AUDIT_INCLUDE_SQL` | `true` |

Server settings: `DATABASE_URL`, `PGMCP_TRANSPORT` (`stdio` or `http`), `HOST`, `PORT`, `ALLOWED_HOSTS`, `ALLOWED_ORIGINS`, `SHARED_SECRET` (enables encrypted `X-DB-URL`).

### Audit log format

```json
{"ts": "2026-11-15T05:42:31.852+00:00", "tool": "run_query", "decision": "allowed", "db": "db.internal:5432/shop", "fingerprint": "9dbbb4ff3937637a", "sql": "SELECT status, count(*) FROM orders GROUP BY 1", "tables": ["orders"], "rows": 4, "duration_ms": 3.1}
{"ts": "2026-11-15T05:42:33.104+00:00", "tool": "run_query", "decision": "blocked", "db": "db.internal:5432/shop", "sql": "SELECT 1; DROP TABLE orders", "reason": "Exactly one statement is allowed per query"}
```

The fingerprint is the same for queries that differ only in literal values, so you can group repeated questions.

## Development

```bash
git clone https://github.com/ig-ViraL/pg-mcp-server
cd pg-mcp-server
uv venv && uv pip install -e ".[dev]"
pytest                                    # unit tests
PGMCP_TEST_DATABASE_URL=postgresql://postgres@localhost:5432/mcptest pytest   # plus integration tests
npm install && npm run dev                # MCP Inspector UI
```

MySQL and SQLite drivers exist as extras (`pip install 'pg-mcp-server[mysql]'`) but use a simpler keyword check; the parser guard, policy and audit features are PostgreSQL only.

## License

MIT. Built by [Viral Deshle](https://www.viral-deshle.cloud).
