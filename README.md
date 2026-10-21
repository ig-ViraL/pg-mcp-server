<div align="center">

# 🐘 pg-mcp-server

**A Model Context Protocol server that lets LLMs explore and query PostgreSQL safely: 10 tools, read-only by design, bring-your-own-database with encrypted connection strings.**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white) ![MCP](https://img.shields.io/badge/MCP-FastMCP-d77757) ![Tests](https://github.com/ig-ViraL/pg-mcp-server/actions/workflows/ci.yml/badge.svg) ![License](https://img.shields.io/badge/License-MIT-green)

</div>

## Highlights

| | |
|:--|:--|
| 🧰 **10 tools** | Schema discovery, query execution, `EXPLAIN` plans, slow-query analysis and DB health checks |
| 🛡️ **Defence in depth** | `SELECT`/`WITH`-only validator (blocks DDL/DML, `SELECT INTO`, row locks, server-side functions, stacked queries, comments), every query in a **read-only transaction**, auto-capped `LIMIT`, 5 s statement timeout, read-only DB role |
| 🔐 **Bring your own DB** | Visitors' connection strings travel as an **AES-256-GCM** token (`X-DB-URL`); nothing is stored server-side |
| 🧠 **Prompt included** | An `nl_to_sql` MCP prompt that pushes models to inspect foreign keys before writing JOINs |
| 🗄️ **Multi-driver** | PostgreSQL (asyncpg), MySQL and SQLite drivers behind one interface |
| ✅ **Tested** | 59 tests (validator, crypto, read-only execution) run in CI |

> Built with Claude Code as a pair programmer; the safety model and architecture are mine. A live chat UI for this server lives in my [portfolio](https://www.viral-deshle.cloud/pg-mcp).

## Tools

| Tool | What it does |
|:--|:--|
| `list_tables` | Tables with columns, types, PKs, FKs, indexes and comments |
| `describe_table` | Deep metadata for one table, plus sample rows and row count |
| `search_schema` | Keyword search across table, column and comment text |
| `get_row_count` | Exact row count for a table |
| `run_query` | Read-only `SELECT`, validated, with auto `LIMIT` and timeout |
| `explain_query` | Execution plan for a query |
| `get_slow_queries` | Slowest statements from `pg_stat_statements` |
| `get_db_health` | Index bloat, long-running queries, connection usage |
| `annotate_table` / `annotate_column` | Attach business descriptions the model can read later |

## Prerequisites

| Requirement | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Running the server |
| Node.js | 18+ | Inspector UI in dev mode only |
| PostgreSQL | any | Your database |

> Node.js is only required for `npm run dev` (the browser UI). The production command has no Node.js dependency.

---

## Setup

```bash
git clone <repo-url>
cd pg-mcp-server

# Python dependencies
pip install -r requirements.txt

# Node.js dependencies (installs MCP Inspector locally — no global npx needed)
npm install

cp .env.example .env
# Edit .env and set your DATABASE_URL
```

`.env` format:

```env
DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/<database>
```

---

## Commands

### Dev — server + Inspector UI

```bash
npm run dev
```

Opens the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) in your browser — a UI for calling tools, inspecting schemas, and viewing raw responses. Uses your `.env` automatically.

The inspector is bundled as a local devDependency (`npm install` above) — no global `npx` or manual installs required. `mcp run` is used as the server command because the inspector requires it to handle the stdio handshake correctly.

---

### Production — SSE server

```bash
npm start
```

Starts the MCP server over SSE transport on port 8000. Run this after deployment. No Node.js required at runtime — `npm start` is just a shortcut for:

```bash
mcp run --transport sse src/server.py:app
```

---

## Connect to Claude Desktop

Add this to your Claude Desktop config file (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "postgres": {
      "command": "mcp",
      "args": ["run", "src/server.py:app"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:password@localhost:5432/mydb"
      }
    }
  }
}
```

Config file locations:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Restart Claude Desktop after saving.

---

## Project Structure

```
pg-mcp-server/
├── src/
│   ├── server.py          # Entry point — defines the MCP app
│   ├── db/
│   │   └── pool.py        # asyncpg connection pool
│   ├── tools/
│   │   ├── run_query.py   # run_query tool handler
│   │   ├── list_tables.py # list_tables tool handler
│   │   └── get_row_count.py # get_row_count tool handler
│   └── safety/
│       └── validator.py   # SQL validation and injection guard
├── tests/
│   └── test_validator.py
├── .env                   # local secrets (git-ignored)
├── .env.example           # config template
├── Dockerfile
├── Procfile               # Railway deployment
└── requirements.txt
```

---


## Safety

Model-written SQL goes through two independent layers, so a bypass of one is caught by the other.

**1. Static checks** (`src/safety/validator.py`)

- Only queries starting with `SELECT` or `WITH` are accepted
- Blocked: `DROP`, `DELETE`, `UPDATE`, `INSERT`, `TRUNCATE`, `ALTER`, `CREATE`, `GRANT`, `REVOKE`, `EXEC`, `EXECUTE`, `COPY`, `MERGE` (this also stops data-modifying CTEs)
- Blocked: `SELECT ... INTO` (creates a table in Postgres, writes a file in MySQL) and row locks (`FOR SHARE`, `FOR KEY SHARE`, `FOR NO KEY UPDATE`)
- Blocked: server-side functions such as `pg_terminate_backend`, `pg_sleep`, `pg_read_file`, `set_config`, `setval`, `lo_*`, `dblink`, advisory locks, MySQL `SLEEP` / `LOAD_FILE`
- Comments (`--`, `/* */`, MySQL `#`) and stacked statements (`; ...`) are blocked
- The outer query always gets a `LIMIT`; a larger trailing `LIMIT` is lowered to the cap
- Table names validated against `^[a-zA-Z_][a-zA-Z0-9_]*$`

**2. Read-only execution** (drivers)

- PostgreSQL: `run_query` and `explain_query` run inside `BEGIN READ ONLY` (`EXPLAIN ANALYZE` executes the query, so it gets the same guard)
- MySQL: `START TRANSACTION READ ONLY`, always rolled back
- SQLite: the query connection is opened with `mode=ro`
- Every query runs with a 5 s statement timeout

For your own database, also use a read-only role (below).

---

## Tests

```bash
pytest tests/ -v
```

---

## Docker

```bash
docker build -t pg-mcp-server .
docker run -e DATABASE_URL=postgresql://user:pass@host:5432/db pg-mcp-server
```

---

## Deploy to Railway

1. Push to GitHub
2. New Railway project → **Deploy from GitHub**
3. Set `DATABASE_URL` in Railway **Variables**
4. Railway picks up the `Procfile` and deploys automatically

---

## PostgreSQL Read-Only Role (recommended for production)

```sql
CREATE ROLE mcp_readonly LOGIN PASSWORD 'strong_password_here';
GRANT CONNECT ON DATABASE your_db TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO mcp_readonly;
```

Then update `DATABASE_URL` to use `mcp_readonly` instead of a superuser.
