import json

from pg_mcp_server import audit
from pg_mcp_server.config import Policy, set_policy, get_policy


def test_audit_writes_jsonl_without_credentials(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    old = get_policy()
    set_policy(Policy(audit_path=str(log)))
    monkeypatch.setenv("DATABASE_URL", "postgresql://alice:s3cret@db.example.com:5432/shop")
    try:
        audit.record("run_query", "allowed", sql="SELECT 1", fingerprint="abc", rows=1, tables=["t"])
        audit.record("run_query", "blocked", sql="DROP TABLE t", reason="Only SELECT")
    finally:
        set_policy(old)
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    assert [l["decision"] for l in lines] == ["allowed", "blocked"]
    assert lines[0]["db"] == "db.example.com:5432/shop"
    assert "s3cret" not in log.read_text() and "alice" not in log.read_text()
    assert lines[0]["rows"] == 1 and lines[1]["reason"] == "Only SELECT"


def test_audit_can_omit_sql(tmp_path):
    log = tmp_path / "audit.jsonl"
    old = get_policy()
    set_policy(Policy(audit_path=str(log), audit_include_sql=False))
    try:
        audit.record("run_query", "allowed", sql="SELECT secret FROM t", fingerprint="fp")
    finally:
        set_policy(old)
    event = json.loads(log.read_text())
    assert "sql" not in event and event["fingerprint"] == "fp"


def test_audit_disabled_by_default():
    old = get_policy()
    set_policy(Policy())
    try:
        assert audit.record("run_query", "allowed") is None
    finally:
        set_policy(old)
