import json

import pytest
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
    StudioWorkspaceWorkItem,
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


def _create_studio(client, **overrides):
    payload = {
        "name": "Legacy Garden",
        "idea": "A puzzle game about restoring old systems.",
    }
    payload.update(overrides)
    response = client.post("/api/studio-workspaces", json=payload)
    assert response.status_code == 200
    return response.json()["workspace"]


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


def test_studio_workspace_mutations_are_owner_scoped(monkeypatch):
    client, _session_local, owner = _client(monkeypatch)
    workspace = _create_studio(
        client,
        name="Scoped Studio",
        idea="A stealth game about clockwork shadows.",
    )
    workspace_id = workspace["id"]

    owner["value"] = "bob"

    advance_response = client.post(f"/api/studio-workspaces/{workspace_id}/advance", json={})
    assert advance_response.status_code == 404

    patch_response = client.patch(
        f"/api/studio-workspaces/{workspace_id}/work-items/not-bobs-item",
        json={"status": "doing"},
    )
    assert patch_response.status_code == 404


@pytest.mark.parametrize("legacy_metadata", [None, "", "{not-json"])
def test_legacy_studio_metadata_defaults_remain_route_compatible(monkeypatch, legacy_metadata):
    client, session_local, _owner = _client(monkeypatch)
    workspace = _create_studio(
        client,
        name="Legacy Metadata",
        idea="A platformer about repairing abandoned save files.",
    )
    workspace_id = workspace["id"]

    db = session_local()
    try:
        persisted = db.query(StudioWorkspace).filter(StudioWorkspace.id == workspace_id).first()
        persisted.workspace_metadata = legacy_metadata
        db.commit()
    finally:
        db.close()

    get_response = client.get(f"/api/studio-workspaces/{workspace_id}")
    assert get_response.status_code == 200
    setup = get_response.json()["workspace"]["metadata"]["setup"]
    assert setup["target_platform"] == "unspecified"
    assert setup["genre"] == "unspecified"
    assert setup["tone"] == "balanced"
    assert setup["scope"] == "prototype"
    assert setup["production_goal"] == ""
    assert setup["selected_roles"] is None

    advance_response = client.post(f"/api/studio-workspaces/{workspace_id}/advance", json={})
    assert advance_response.status_code == 200
    advanced = advance_response.json()["workspace"]
    assert len(advanced["work_items"]) == 8

    db = session_local()
    try:
        persisted = db.query(StudioWorkspace).filter(StudioWorkspace.id == workspace_id).first()
        persisted_metadata = json.loads(persisted.workspace_metadata)
        assert persisted_metadata["setup"]["target_platform"] == "unspecified"
        assert persisted_metadata["setup"]["selected_roles"] is None
    finally:
        db.close()


def test_advance_studio_workspace_creates_production_board(monkeypatch):
    client, session_local, _owner = _client(monkeypatch)
    create_response = client.post(
        "/api/studio-workspaces",
        json={"name": "Solar Courier", "idea": "A delivery game across tiny orbiting cities."},
    )
    assert create_response.status_code == 200
    workspace_id = create_response.json()["workspace"]["id"]

    advance_response = client.post(f"/api/studio-workspaces/{workspace_id}/advance", json={})

    assert advance_response.status_code == 200
    workspace = advance_response.json()["workspace"]
    assert workspace["phase"] == "production_planning"
    assert workspace["status"] == "active"
    assert len(workspace["artifacts"]) == 9
    assert len(workspace["work_items"]) == 8
    assert {item["status"] for item in workspace["work_items"]} == {"todo"}

    item_id = workspace["work_items"][0]["id"]
    patch_response = client.patch(
        f"/api/studio-workspaces/{workspace_id}/work-items/{item_id}",
        json={"status": "doing", "priority": "high"},
    )
    assert patch_response.status_code == 200
    patched_item = next(
        item for item in patch_response.json()["workspace"]["work_items"]
        if item["id"] == item_id
    )
    assert patched_item["status"] == "doing"
    assert patched_item["priority"] == "high"

    db = session_local()
    try:
        assert db.query(StudioWorkspaceArtifact).count() == 9
        assert db.query(StudioWorkspaceWorkItem).count() == 8
        assert db.query(Document).count() == 9
        assert db.query(DocumentVersion).count() == 9
    finally:
        db.close()


def test_create_studio_workspace_persists_setup_customization(monkeypatch):
    client, session_local, _owner = _client(monkeypatch)

    response = client.post(
        "/api/studio-workspaces",
        json={
            "name": "Pocket Arena",
            "idea": "A fast web tactics game for short sessions.",
            "target_platform": "Web",
            "genre": "Tactics",
            "tone": "Competitive but playful",
            "scope": "Tiny",
            "production_goal": "Prototype",
            "selected_roles": ["product_lead", "game_designer", "tech_lead"],
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert len(workspace["team"]) == 3
    assert workspace["metadata"]["setup"]["target_platform"] == "Web"
    assert workspace["metadata"]["setup"]["genre"] == "Tactics"
    assert workspace["metadata"]["setup"]["selected_roles"] == [
        "product_lead",
        "game_designer",
        "tech_lead",
    ]

    db = session_local()
    try:
        crews = db.query(CrewMember).all()
        assert len(crews) == 3
        assert "Target platform: Web" in crews[0].personality
        assert db.query(StudioWorkspaceAgent).count() == 3
    finally:
        db.close()


def test_create_studio_workspace_accepts_role_names(monkeypatch):
    client, _session_local, _owner = _client(monkeypatch)

    response = client.post(
        "/api/studio-workspaces",
        json={
            "name": "Signal Kitchen",
            "idea": "A tiny cooking game where recipes are radio puzzles.",
            "selected_roles": ["Product Lead", "Tech Lead"],
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert [member["role_key"] for member in workspace["team"]] == [
        "product_lead",
        "tech_lead",
    ]
    assert workspace["metadata"]["setup"]["selected_roles"] == [
        "product_lead",
        "tech_lead",
    ]


def test_create_studio_workspace_with_selected_roles_only_uses_defaults(monkeypatch):
    client, session_local, _owner = _client(monkeypatch)

    response = client.post(
        "/api/studio-workspaces",
        json={
            "idea": "A lightweight tactics game about shifting lanes.",
            "selected_roles": ["game_designer", "qa_lead"],
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert [member["role_key"] for member in workspace["team"]] == [
        "game_designer",
        "qa_lead",
    ]
    setup = workspace["metadata"]["setup"]
    assert setup["target_platform"] == "unspecified"
    assert setup["genre"] == "unspecified"
    assert setup["tone"] == "balanced"
    assert setup["scope"] == "prototype"
    assert setup["selected_roles"] == ["game_designer", "qa_lead"]

    db = session_local()
    try:
        persisted = db.query(StudioWorkspace).first()
        persisted_metadata = json.loads(persisted.workspace_metadata)
        assert persisted_metadata["setup"]["selected_roles"] == [
            "game_designer",
            "qa_lead",
        ]
        assert db.query(StudioWorkspaceAgent).count() == 2
    finally:
        db.close()


def test_advance_studio_workspace_creates_board_for_selected_roles_only(monkeypatch):
    client, session_local, _owner = _client(monkeypatch)
    workspace = _create_studio(
        client,
        name="Lean Team",
        idea="A compact tactics game about swapping gravity.",
        selected_roles=["product_lead", "gameplay_engineer", "producer"],
    )

    advance_response = client.post(f"/api/studio-workspaces/{workspace['id']}/advance", json={})

    assert advance_response.status_code == 200
    advanced = advance_response.json()["workspace"]
    assert len(advanced["work_items"]) == 3
    assert {item["role_key"] for item in advanced["work_items"]} == {
        "product_lead",
        "gameplay_engineer",
        "producer",
    }

    db = session_local()
    try:
        assert {
            item.role_key
            for item in db.query(StudioWorkspaceWorkItem).filter(
                StudioWorkspaceWorkItem.workspace_id == workspace["id"],
            ).all()
        } == {"product_lead", "gameplay_engineer", "producer"}
    finally:
        db.close()


def test_create_studio_workspace_rejects_unknown_selected_roles(monkeypatch):
    client, session_local, _owner = _client(monkeypatch)

    response = client.post(
        "/api/studio-workspaces",
        json={
            "name": "Bad Stack",
            "idea": "A puzzle game about suspicious team setup.",
            "selected_roles": ["product_lead", "Mystery Role"],
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "invalid_selected_roles"
    assert detail["invalid_roles"] == ["Mystery Role"]

    db = session_local()
    try:
        assert db.query(StudioWorkspace).count() == 0
        assert db.query(StudioWorkspaceAgent).count() == 0
        assert db.query(CrewMember).count() == 0
    finally:
        db.close()


def test_advance_sanitizes_selected_roles_from_existing_metadata(monkeypatch):
    client, session_local, _owner = _client(monkeypatch)
    create_response = client.post(
        "/api/studio-workspaces",
        json={
            "name": "Subset Studio",
            "idea": "A roguelite about arranging constellations.",
            "selected_roles": ["product_lead", "tech_lead"],
        },
    )
    assert create_response.status_code == 200
    workspace_id = create_response.json()["workspace"]["id"]

    db = session_local()
    try:
        workspace = db.query(StudioWorkspace).filter(StudioWorkspace.id == workspace_id).first()
        metadata = json.loads(workspace.workspace_metadata)
        metadata["setup"]["selected_roles"] = [
            "Product Lead",
            "Bogus Role",
            "tech_lead",
            "QA Lead",
        ]
        workspace.workspace_metadata = json.dumps(metadata)
        db.commit()
    finally:
        db.close()

    advance_response = client.post(f"/api/studio-workspaces/{workspace_id}/advance", json={})

    assert advance_response.status_code == 200
    workspace = advance_response.json()["workspace"]
    assert workspace["metadata"]["setup"]["selected_roles"] == [
        "product_lead",
        "tech_lead",
    ]
    assert {item["role_key"] for item in workspace["work_items"]} == {
        "product_lead",
        "tech_lead",
    }

    db = session_local()
    try:
        persisted = db.query(StudioWorkspace).filter(StudioWorkspace.id == workspace_id).first()
        persisted_metadata = json.loads(persisted.workspace_metadata)
        assert persisted_metadata["setup"]["selected_roles"] == [
            "product_lead",
            "tech_lead",
        ]
    finally:
        db.close()
