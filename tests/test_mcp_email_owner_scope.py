import sqlite3

import pytest


def _create_email_accounts_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE email_accounts (
            id TEXT PRIMARY KEY,
            owner TEXT,
            name TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            imap_host TEXT,
            imap_port INTEGER,
            imap_user TEXT,
            imap_password TEXT,
            imap_starttls INTEGER,
            smtp_host TEXT,
            smtp_port INTEGER,
            smtp_security TEXT,
            smtp_user TEXT,
            smtp_password TEXT,
            from_address TEXT,
            created_at TEXT
        )
        """
    )
    rows = [
        ("acct-alice", "alice", "Work", 1, "alice@example.com"),
        ("acct-bob", "bob", "Work", 1, "bob@example.com"),
        ("acct-legacy-alice", "", "Legacy", 0, "alice"),
        ("acct-legacy-other", "", "Legacy Other", 0, "shared@example.com"),
    ]
    for account_id, owner, name, is_default, imap_user in rows:
        conn.execute(
            """
            INSERT INTO email_accounts
            (id, owner, name, is_default, enabled, imap_host, imap_port,
             imap_user, imap_password, imap_starttls, smtp_host, smtp_port,
             smtp_security, smtp_user, smtp_password, from_address, created_at)
            VALUES (?, ?, ?, ?, 1, 'imap.example.com', 993, ?, 'secret', 0,
                    'smtp.example.com', 465, 'ssl', ?, 'secret', ?, ?)
            """,
            (
                account_id,
                owner,
                name,
                is_default,
                imap_user,
                imap_user,
                imap_user,
                "2026-01-01T00:00:00",
            ),
        )
    conn.commit()
    conn.close()


def test_mcp_email_accounts_are_scoped_to_tool_owner(tmp_path, monkeypatch):
    import mcp_servers.email_server as email_server

    db_path = tmp_path / "app.db"
    _create_email_accounts_db(db_path)
    monkeypatch.setattr(email_server, "APP_DB", str(db_path))
    email_server._ACCOUNT_CACHE.clear()

    rows = email_server._list_accounts_raw(owner="alice")

    assert {row["id"] for row in rows} == {"acct-alice", "acct-legacy-alice"}


def test_mcp_email_config_cache_is_separated_by_owner(tmp_path, monkeypatch):
    import mcp_servers.email_server as email_server

    db_path = tmp_path / "app.db"
    _create_email_accounts_db(db_path)
    monkeypatch.setattr(email_server, "APP_DB", str(db_path))
    email_server._ACCOUNT_CACHE.clear()

    alice_cfg = email_server._load_config("Work", owner="alice")
    bob_cfg = email_server._load_config("Work", owner="bob")

    assert alice_cfg["account_id"] == "acct-alice"
    assert alice_cfg["imap_user"] == "alice@example.com"
    assert bob_cfg["account_id"] == "acct-bob"
    assert bob_cfg["imap_user"] == "bob@example.com"


@pytest.mark.asyncio
async def test_mcp_manager_injects_owner_only_for_builtin_email(monkeypatch):
    from src.mcp_manager import McpManager

    manager = McpManager()
    manager._sessions["email"] = object()
    manager._sessions["other"] = object()
    captured = {}

    async def fake_do_call(_session, tool_name, arguments):
        captured[tool_name] = dict(arguments)
        return {"stdout": "ok", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(manager, "_do_call", fake_do_call)

    await manager.call_tool("mcp__email__list_email_accounts", {}, owner="alice")
    await manager.call_tool("mcp__other__some_tool", {}, owner="alice")

    assert captured["list_email_accounts"]["_odysseus_owner"] == "alice"
    assert "_odysseus_owner" not in captured["some_tool"]
