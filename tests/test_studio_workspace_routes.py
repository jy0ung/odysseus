from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.studio_workspace_routes as studio_routes
from core.database import (
    Base,
    CrewMember,
    Document,
    DocumentVersion,
    StudioWorkspace,
    StudioWorkspaceAgent,
    StudioWorkspaceArtifact,
)
from tests.helpers.sqlite_db import make_temp_sqlite


def _client(monkeypatch, owner="alice"):
    session_local, _engine, _tmpfile = make_temp_sqlite(Base.metadata)
    current_owner = {"value": owner}
    monkeypatch.setattr(studio_routes, "SessionLocal", session_local)
    monkeypatch.setattr(studio_routes, "require_user", lambda request: current_owner["value"])
    app = FastAPI()
    app.include_router(studio_routes.setup_studio_workspace_routes())
    return TestClient(app), session_local, current_owner


def test_create_studio_workspace_seeds_team_and_artifacts(monkeypatch):
    client, session_local, _owner = _client(monkeypatch)

    response = client.post(
        "/api/studio-workspaces",
        json={
            "name": "Clockwork Orchard",
            "idea": "A cozy automation game about restoring a mechanical orchard.",
            "preset": "game_dev",
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert workspace["name"] == "Clockwork Orchard"
    assert len(workspace["team"]) == 8
    assert len(workspace["artifacts"]) == 4
    assert {artifact["title"] for artifact in workspace["artifacts"]} == {
        "Vision Brief",
        "Game Design Outline",
        "Production Plan",
        "Task Backlog",
    }

    db = session_local()
    try:
        assert db.query(StudioWorkspace).count() == 1
        assert db.query(StudioWorkspaceAgent).count() == 8
        assert db.query(StudioWorkspaceArtifact).count() == 4
        assert db.query(CrewMember).count() == 8
        assert db.query(Document).count() == 4
        assert db.query(DocumentVersion).count() == 4
        assert {doc.owner for doc in db.query(Document).all()} == {"alice"}
    finally:
        db.close()


def test_studio_workspace_list_is_owner_scoped(monkeypatch):
    client, _session_local, owner = _client(monkeypatch)
    response = client.post(
        "/api/studio-workspaces",
        json={"idea": "A tactics game where spells rewrite the battlefield."},
    )
    assert response.status_code == 200
    workspace_id = response.json()["workspace"]["id"]

    owner["value"] = "bob"
    list_response = client.get("/api/studio-workspaces")
    assert list_response.status_code == 200
    assert list_response.json()["workspaces"] == []

    get_response = client.get(f"/api/studio-workspaces/{workspace_id}")
    assert get_response.status_code == 404
