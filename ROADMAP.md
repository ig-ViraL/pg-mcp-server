# pg-mcp-server — Feature Roadmap

> Research date: 2026-05-05
> Goal: Make this the best NL-to-SQL MCP server by adding schema richness, prompt resources, and a business metadata layer that competitors lack.

---

## Current State (Oct 2026)

All P0 and P1 items below have shipped: 10 tools (`list_tables`, `describe_table`, `search_schema`, `get_row_count`, `run_query`, `explain_query`, `get_slow_queries`, `get_db_health`, `annotate_table`, `annotate_column`), the `nl_to_sql` prompt, PostgreSQL / MySQL / SQLite drivers, and two-layer query safety (static validator + read-only transactions). The sections below are kept as the original research notes.

## Next phases

1. **Distribution:** publish to PyPI so it runs with `uvx pg-mcp-server`, and list it in the MCP server registries.
2. **Accuracy evals:** 30 natural-language questions against the seeded demo DB with expected results; report accuracy per model in the README.
3. **Parser-based validation:** switch the regex validator to `pglast` (AST check for `SelectStmt` only). The read-only transaction already makes a bypass harmless; this removes false positives on string literals.
4. **Query history resource** and **golden SQL** store (see P2).

---

## P0 — Quick wins, high accuracy impact

### 1. Enrich `list_tables`

Currently only returns column name + type. Add:
- Primary keys
- Foreign key relationships (with referenced table + column)
- Column comments from `pg_description`
- Index names + types

**Why:** This single change lets Claude infer JOINs automatically. The #1 accuracy driver for NL-to-SQL.

**Query to use:** `pg_catalog.pg_class`, `pg_attribute`, `pg_description`, `pg_constraint` instead of `information_schema.columns`.

---

### 2. New tool: `describe_table`

Deep metadata for a single table. Call this before writing any JOIN query.

Returns:
- All columns (name, type, nullable, default)
- Primary key columns
- Foreign keys (column → referenced_table.referenced_column)
- Indexes (name, type, unique, columns covered)
- Sample rows (3–5 rows)
- Row count estimate
- Table comment (from `pg_description`)

**Usage pattern:** Claude calls `list_tables` first to get the full picture, then `describe_table` on the relevant tables before generating SQL.

---

### 3. New tool: `search_schema`

Full-text search across table names, column names, and column comments.

```
search_schema(query="customer revenue") 
→ returns matching tables/columns ranked by relevance
```

**Why:** Essential once the DB has 50+ tables. Avoids dumping the entire schema into every prompt.

---

## P1 — Performance + DBA tools

### 4. New tool: `explain_query`

Run `EXPLAIN (ANALYZE false, FORMAT JSON)` on a SELECT — no actual execution, just the plan.

Optional `analyze=true` parameter for real execution stats (use with caution on large tables).

**Returns:** Structured JSON plan that Claude can reason about to suggest index changes.

---

### 5. New tool: `get_slow_queries`

Query `pg_stat_statements` for the top-N slowest queries by total execution time.

```
get_slow_queries(limit=10)
→ query text, calls, mean_exec_time, total_exec_time
```

Check for extension presence first; return a helpful error if `pg_stat_statements` is not installed.

---

### 6. New tool: `get_db_health`

No extension required. Uses catalog tables only.

Checks:
- Unused indexes (`pg_stat_user_indexes` where `idx_scan = 0`)
- Table bloat (`pg_stat_user_tables` — dead tuples vs live tuples ratio)
- Long-running queries (`pg_stat_activity` where `state != 'idle'`)
- Connection utilization vs `max_connections`

---

## P1 — The Big Differentiator: Business Metadata Layer

### 7. New tool: `annotate_table` and `annotate_column`

Write Postgres native comments — no external DB or vector store needed.

```sql
COMMENT ON TABLE orders IS 'Customer purchase transactions';
COMMENT ON COLUMN orders.status IS 'One of: pending, shipped, delivered, cancelled';
```

**The flywheel:**
1. User asks "what does `cust_ltv` mean?"
2. Claude calls `annotate_column("customers", "cust_ltv", "Lifetime value in USD")`
3. Next query, `list_tables` already returns that context
4. The server gets smarter about *this specific database* over time

**Why nobody else has this:** Every competitor surfaces raw technical schema. None of them write business context back into Postgres natively. This is Vanna.ai's core moat — replicated without a vector DB.

---

## P0 — MCP Prompt Resource

### 8. Expose `nl_to_sql` as an MCP prompt resource

No competitor does this. Expose a reusable system prompt template that Claude can retrieve via MCP.

**Rules to bake in:**
```
You are a PostgreSQL expert generating read-only SELECT queries.

Rules:
- NEVER use SELECT * — select only the columns needed
- ALWAYS alias tables in JOINs (e.g. customers c, orders o)
- ALWAYS add LIMIT for exploratory queries (default 100)
- Call describe_table before writing any JOIN query
- If the question is ambiguous, ask ONE clarifying question instead of guessing
- When you make an assumption, state it explicitly before the SQL
- Prefer window functions over nested subqueries when equivalent
- Use date_trunc() for time bucketing, not EXTRACT()

Chain-of-thought before SQL:
1. Identify the relevant tables
2. Identify the JOIN path using foreign keys
3. Identify filters and aggregations needed
4. Write the SQL
```

---

## P2 — Future Ideas

| Idea | Notes |
|---|---|
| Query history resource | Log last 50 NL questions + SQL. Expose as MCP resource `postgres://query_history`. Foundation for golden SQL store. |
| pgvector similarity search | Templated `SELECT ... ORDER BY embedding <=> $1 LIMIT k` tool for AI apps storing embeddings |
| Named parameterized queries | YAML/TOML config to pre-approve safe reusable queries (like DBHub's approach) |
| Multi-schema support | Optional `schema` param on `run_query` and `list_tables` |
| Switch validator to pglast | Current regex can be bypassed with creative quoting. `pglast` parses the SQL AST and checks `SelectStmt` — much more robust |

---

## Competitor Snapshot

| Server | Stars | Key strength | Key gap |
|---|---|---|---|
| DBHub (Bytebase) | 2K+ | Minimal, multi-DB, most adopted | No NL-to-SQL, no metadata |
| Postgres MCP Pro | — | EXPLAIN + index recommendations | Complex setup, no NL |
| pgmcp | — | Built-in NL-to-SQL (GPT) | OpenAI dependency |
| postgres-mcp (neverinfamous) | — | 248 tools, OAuth 2.1 | Extreme complexity |
| **Our server** | — | Railway deploy, clean safety layer | Missing rich schema + prompts |

**Our gap to own:** Business metadata flywheel (annotate → enrich → smarter queries) + MCP prompt resource. No one has both.
