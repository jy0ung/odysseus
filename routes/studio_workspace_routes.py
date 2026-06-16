"""Studio workspace routes.

A studio workspace turns a rough product idea into a concrete starting point:
role-specific CrewMember personas plus editable planning documents.
"""

import json
import re
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.database import (
    CrewMember,
    Document,
    DocumentVersion,
    SessionLocal,
    StudioWorkspace,
    StudioWorkspaceAgent,
    StudioWorkspaceArtifact,
)
from src.auth_helpers import require_user


class StudioWorkspaceCreate(BaseModel):
    idea: str = Field(..., min_length=3)
    name: Optional[str] = None
    preset: str = "game_dev"


GAME_DEV_ROLES = [
    {
        "key": "product_lead",
        "name": "Product Lead",
        "avatar": "PL",
        "mission": "Turn the rough idea into a coherent audience, promise, scope, and launch thesis.",
        "tools": ["create_document", "update_document", "suggest_document", "manage_tasks"],
    },
    {
        "key": "game_designer",
        "name": "Game Designer",
        "avatar": "GD",
        "mission": "Define the core loop, player decisions, systems, progression, and fun risks.",
        "tools": ["create_document", "update_document", "suggest_document", "manage_tasks"],
    },
    {
        "key": "narrative_designer",
        "name": "Narrative Designer",
        "avatar": "ND",
        "mission": "Shape the setting, emotional arc, characters, tone, and lore constraints.",
        "tools": ["create_document", "update_document", "suggest_document"],
    },
    {
        "key": "art_director",
        "name": "Art Director",
        "avatar": "AD",
        "mission": "Translate the idea into visual pillars, asset needs, UI tone, and style references.",
        "tools": ["create_document", "update_document", "suggest_document"],
    },
    {
        "key": "tech_lead",
        "name": "Tech Lead",
        "avatar": "TL",
        "mission": "Choose the architecture, engine assumptions, data model, risks, and integration plan.",
        "tools": ["create_document", "update_document", "suggest_document", "read_file"],
    },
    {
        "key": "gameplay_engineer",
        "name": "Gameplay Engineer",
        "avatar": "GE",
        "mission": "Break mechanics into buildable systems, prototypes, acceptance tests, and implementation tasks.",
        "tools": ["create_document", "update_document", "suggest_document", "manage_tasks", "read_file"],
    },
    {
        "key": "qa_lead",
        "name": "QA Lead",
        "avatar": "QA",
        "mission": "Identify quality bars, test strategy, edge cases, performance budgets, and release gates.",
        "tools": ["create_document", "update_document", "suggest_document", "manage_tasks"],
    },
    {
        "key": "producer",
        "name": "Producer",
        "avatar": "PR",
        "mission": "Sequence the work into milestones, dependencies, staffing assumptions, and decision checkpoints.",
        "tools": ["create_document", "update_document", "suggest_document", "manage_tasks"],
    },
]

PRESETS = {
    "game_dev": {
        "key": "game_dev",
        "name": "Game Development Stack",
        "description": "A compact production team for turning a game idea into specs, plans, and build tasks.",
        "roles": GAME_DEV_ROLES,
    }
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _owner_value(owner: str) -> Optional[str]:
    return owner or None


def _iso(dt) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def _derive_name(idea: str) -> str:
    first_line = (idea or "").strip().splitlines()[0].strip()
    words = re.findall(r"[A-Za-z0-9']+", first_line)
    if not words:
        return "Untitled Studio"
    title = " ".join(words[:7]).strip()
    return title[:64] or "Untitled Studio"


def _role_personality(role: dict, workspace_name: str, idea: str) -> str:
    return (
        f"You are the {role['name']} for the '{workspace_name}' studio workspace.\n"
        f"Mission: {role['mission']}\n\n"
        "Work as a senior production teammate. Be specific, ask for missing decisions, "
        "surface risks early, and keep outputs ready for implementation.\n\n"
        f"Current rough idea:\n{idea.strip()}"
    )


def _artifact_markdown(kind: str, title: str, idea: str, workspace_name: str) -> str:
    idea = idea.strip()
    if kind == "vision_brief":
        return f"""# {workspace_name} Vision Brief

## Raw Idea
{idea}

## Product Promise
- Player fantasy:
- Target audience:
- Platform assumptions:
- Session length:
- Why now:

## Non-Negotiables
- Core feeling:
- Scope boundary:
- Quality bar:

## Open Decisions
- What is the smallest playable version?
- What makes this distinct from nearby games?
- What must be validated before production?
"""
    if kind == "game_design_outline":
        return f"""# {workspace_name} Game Design Outline

## Core Loop
1. Observe
2. Decide
3. Act
4. Earn feedback
5. Upgrade or adapt

## Player Systems
- Movement:
- Interaction:
- Progression:
- Economy:
- Failure and recovery:

## Content Pillars
- Mechanics:
- Levels or encounters:
- Narrative beats:
- UI and feedback:

## Prototype Questions
- Is the core loop fun in 5 minutes?
- Which mechanic needs to exist first?
- What can be faked for the first build?
"""
    if kind == "production_plan":
        return f"""# {workspace_name} Production Plan

## Milestone 0: Discovery
- Lock audience and platform assumptions.
- Build a one-page creative brief.
- Identify technical unknowns.

## Milestone 1: Playable Prototype
- Implement the smallest core loop.
- Use placeholder assets.
- Capture measurable fun and usability feedback.

## Milestone 2: Vertical Slice
- Add representative art, audio, UI, and content.
- Validate performance and pipeline risk.
- Decide ship scope.

## Release Gates
- Fun validated:
- Technical risk retired:
- Content pipeline proven:
- QA pass criteria:
"""
    return f"""# {workspace_name} Task Backlog

## Now
- Product Lead: Write the one-sentence promise and target player.
- Game Designer: Define the smallest playable loop.
- Tech Lead: Pick engine assumptions and identify risky integrations.
- Producer: Convert unknowns into a one-week discovery plan.

## Next
- Narrative Designer: Draft setting pillars and tone constraints.
- Art Director: Collect visual pillars and asset categories.
- Gameplay Engineer: Break the prototype into systems and acceptance criteria.
- QA Lead: Draft test charters for the prototype.

## Later
- Build vertical slice roadmap.
- Define content production pipeline.
- Create launch readiness checklist.
"""


def _initial_artifacts(idea: str, workspace_name: str) -> list[dict]:
    specs = [
        ("vision_brief", "Vision Brief"),
        ("game_design_outline", "Game Design Outline"),
        ("production_plan", "Production Plan"),
        ("task_backlog", "Task Backlog"),
    ]
    return [
        {
            "kind": kind,
            "title": title,
            "content": _artifact_markdown(kind, title, idea, workspace_name),
        }
        for kind, title in specs
    ]


def _create_document(db, *, owner: Optional[str], title: str, content: str) -> Document:
    doc = Document(
        id=_new_id(),
        owner=owner,
        title=title,
        language="markdown",
        current_content=content,
        version_count=1,
        is_active=True,
    )
    version = DocumentVersion(
        id=_new_id(),
        document_id=doc.id,
        version_number=1,
        content=content,
        summary="Seeded by Studio Workspace",
        source="ai",
    )
    db.add(doc)
    db.add(version)
    return doc


def _workspace_to_dict(db, workspace: StudioWorkspace) -> dict:
    agents = db.query(StudioWorkspaceAgent).filter(
        StudioWorkspaceAgent.workspace_id == workspace.id,
    ).order_by(StudioWorkspaceAgent.sort_order.asc()).all()
    artifacts = db.query(StudioWorkspaceArtifact).filter(
        StudioWorkspaceArtifact.workspace_id == workspace.id,
    ).order_by(StudioWorkspaceArtifact.sort_order.asc()).all()
    crew_ids = [a.crew_member_id for a in agents if a.crew_member_id]
    crew_by_id = {}
    if crew_ids:
        crew_by_id = {
            c.id: c
            for c in db.query(CrewMember).filter(CrewMember.id.in_(crew_ids)).all()
        }

    return {
        "id": workspace.id,
        "owner": workspace.owner,
        "name": workspace.name,
        "preset": workspace.preset,
        "idea": workspace.idea,
        "status": workspace.status,
        "phase": workspace.phase,
        "summary": workspace.summary,
        "current_focus": workspace.current_focus,
        "created_at": _iso(workspace.created_at),
        "updated_at": _iso(workspace.updated_at),
        "team": [
            {
                "id": agent.id,
                "role_key": agent.role_key,
                "role_name": agent.role_name,
                "mission": agent.mission,
                "sort_order": agent.sort_order,
                "status": agent.status,
                "crew_member_id": agent.crew_member_id,
                "crew_name": crew_by_id.get(agent.crew_member_id).name if agent.crew_member_id in crew_by_id else None,
                "avatar": crew_by_id.get(agent.crew_member_id).avatar if agent.crew_member_id in crew_by_id else None,
            }
            for agent in agents
        ],
        "artifacts": [
            {
                "id": artifact.id,
                "kind": artifact.kind,
                "title": artifact.title,
                "status": artifact.status,
                "document_id": artifact.document_id,
                "sort_order": artifact.sort_order,
            }
            for artifact in artifacts
        ],
    }


def setup_studio_workspace_routes() -> APIRouter:
    router = APIRouter(prefix="/api/studio-workspaces", tags=["studio-workspaces"])

    @router.get("/presets")
    async def list_presets(request: Request):
        require_user(request)
        return {"presets": list(PRESETS.values())}

    @router.get("")
    async def list_workspaces(request: Request):
        owner = require_user(request)
        db = SessionLocal()
        try:
            query = db.query(StudioWorkspace)
            if owner:
                query = query.filter(StudioWorkspace.owner == owner)
            workspaces = query.order_by(StudioWorkspace.updated_at.desc()).all()
            return {"workspaces": [_workspace_to_dict(db, ws) for ws in workspaces]}
        finally:
            db.close()

    @router.post("")
    async def create_workspace(payload: StudioWorkspaceCreate, request: Request):
        owner = require_user(request)
        owner_for_rows = _owner_value(owner)
        preset = PRESETS.get(payload.preset)
        if not preset:
            raise HTTPException(status_code=400, detail=f"Unknown studio preset: {payload.preset}")

        idea = payload.idea.strip()
        name = (payload.name or "").strip() or _derive_name(idea)
        workspace = StudioWorkspace(
            id=_new_id(),
            owner=owner_for_rows,
            name=name,
            preset=preset["key"],
            idea=idea,
            status="draft",
            phase="intake",
            summary="Studio created. Team and seed documents are ready for expansion.",
            current_focus="Clarify the smallest playable product and first prototype.",
            workspace_metadata=json.dumps({"preset_version": 1}),
        )

        db = SessionLocal()
        try:
            db.add(workspace)
            for index, role in enumerate(preset["roles"]):
                crew = CrewMember(
                    id=_new_id(),
                    owner=owner_for_rows,
                    name=f"{name} - {role['name']}",
                    avatar=role["avatar"],
                    user_name=None,
                    personality=_role_personality(role, name, idea),
                    greeting=f"Ready as {role['name']} for {name}.",
                    enabled_tools=json.dumps(role["tools"]),
                    is_active=True,
                    sort_order=index,
                    is_default_assistant=False,
                )
                agent = StudioWorkspaceAgent(
                    id=_new_id(),
                    workspace_id=workspace.id,
                    crew_member_id=crew.id,
                    owner=owner_for_rows,
                    role_key=role["key"],
                    role_name=role["name"],
                    mission=role["mission"],
                    sort_order=index,
                    status="active",
                )
                db.add(crew)
                db.add(agent)

            for index, artifact_spec in enumerate(_initial_artifacts(idea, name)):
                doc = _create_document(
                    db,
                    owner=owner_for_rows,
                    title=f"{name} - {artifact_spec['title']}",
                    content=artifact_spec["content"],
                )
                artifact = StudioWorkspaceArtifact(
                    id=_new_id(),
                    workspace_id=workspace.id,
                    document_id=doc.id,
                    owner=owner_for_rows,
                    kind=artifact_spec["kind"],
                    title=artifact_spec["title"],
                    sort_order=index,
                    status="draft",
                )
                db.add(artifact)

            db.commit()
            return {"workspace": _workspace_to_dict(db, workspace)}
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to create studio workspace: {exc}")
        finally:
            db.close()

    @router.get("/{workspace_id}")
    async def get_workspace(workspace_id: str, request: Request):
        owner = require_user(request)
        db = SessionLocal()
        try:
            workspace = db.query(StudioWorkspace).filter(StudioWorkspace.id == workspace_id).first()
            if not workspace or (owner and workspace.owner != owner):
                raise HTTPException(status_code=404, detail="Studio workspace not found")
            return {"workspace": _workspace_to_dict(db, workspace)}
        finally:
            db.close()

    return router
