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
    StudioWorkspaceWorkItem,
)
from src.auth_helpers import require_user


class StudioWorkspaceCreate(BaseModel):
    idea: str = Field(..., min_length=3)
    name: Optional[str] = None
    preset: str = "game_dev"
    target_platform: Optional[str] = None
    genre: Optional[str] = None
    tone: Optional[str] = None
    scope: Optional[str] = None
    production_goal: Optional[str] = None
    selected_roles: Optional[list[str]] = None


class StudioWorkspaceAdvance(BaseModel):
    notes: Optional[str] = None


class StudioWorkItemUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None


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


def _clean_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _metadata_from_payload(payload: StudioWorkspaceCreate, selected_role_keys: list[str]) -> dict:
    platform = _clean_optional(payload.target_platform)
    setup = {
        "platform": platform,
        "target_platform": platform,
        "genre": _clean_optional(payload.genre),
        "tone": _clean_optional(payload.tone),
        "scope": _clean_optional(payload.scope),
        "production_goal": _clean_optional(payload.production_goal),
        "selected_roles": selected_role_keys,
    }
    return {
        "preset_version": 1,
        "setup": {k: v for k, v in setup.items() if v not in (None, "", [])},
    }


_STUDIO_SETUP_DEFAULTS = {
    "platform": "unspecified",
    "genre": "unspecified",
    "tone": "balanced",
    "scope": "prototype",
    "production_goal": "",
    "selected_roles": None,
}


def _metadata_text(value, default: str) -> str:
    if value is None:
        return default
    cleaned = str(value).strip()
    return cleaned or default


def _role_token(value) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _known_studio_roles() -> list[dict]:
    roles_by_key = {}
    for preset in PRESETS.values():
        for role in preset["roles"]:
            roles_by_key.setdefault(role["key"], role)
    return list(roles_by_key.values())


def _agent_roles(agents) -> list[dict]:
    return [
        {
            "key": agent.role_key,
            "name": agent.role_name,
            "avatar": None,
        }
        for agent in agents
        if agent.role_key
    ]


def _role_lookup(roles: list[dict]) -> dict[str, str]:
    lookup = {}
    for role in roles:
        key = str(role.get("key", "")).strip()
        if not key:
            continue
        for alias in (role.get("key"), role.get("name"), role.get("avatar")):
            token = _role_token(alias or "")
            if token:
                lookup[token] = key
    return lookup


def _selected_role_error(invalid_roles: list[str]) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": "invalid_selected_roles",
            "message": "Unknown Studio role selected.",
            "invalid_roles": invalid_roles,
        },
    )


def _normalize_selected_role_keys(
    value,
    roles: list[dict],
    *,
    reject_unknown: bool,
    allowed_role_keys: Optional[set[str]] = None,
) -> Optional[list[str]]:
    if not isinstance(value, list):
        return None

    lookup = _role_lookup(roles)
    allowed = set(allowed_role_keys or [])
    selected_keys = []
    invalid_roles = []
    for role in value:
        raw = str(role).strip()
        if not raw:
            continue
        key = lookup.get(_role_token(raw))
        if not key or (allowed and key not in allowed):
            invalid_roles.append(raw)
            continue
        if key not in selected_keys:
            selected_keys.append(key)

    if invalid_roles and reject_unknown:
        raise _selected_role_error(invalid_roles)
    return selected_keys or None


def _safe_workspace_metadata(
    workspace_or_raw,
    *,
    roles: Optional[list[dict]] = None,
    allowed_role_keys: Optional[set[str]] = None,
) -> dict:
    """Parse Studio metadata and normalize legacy/malformed rows.

    Legacy workspaces may have NULL/blank/malformed metadata or missing setup
    keys. Route handlers should use this helper rather than reading
    StudioWorkspace.workspace_metadata directly so every path sees the same
    defaults.
    """
    raw = getattr(workspace_or_raw, "workspace_metadata", workspace_or_raw)
    if isinstance(raw, dict):
        metadata = dict(raw)
    else:
        try:
            metadata = json.loads(raw or "{}")
            if not isinstance(metadata, dict):
                metadata = {}
        except Exception:
            metadata = {}

    setup_raw = metadata.get("setup")
    setup = dict(setup_raw) if isinstance(setup_raw, dict) else {}
    platform = _metadata_text(
        setup.get("platform", setup.get("target_platform")),
        _STUDIO_SETUP_DEFAULTS["platform"],
    )
    normalized_setup = {
        "platform": platform,
        "target_platform": platform,
        "genre": _metadata_text(setup.get("genre"), _STUDIO_SETUP_DEFAULTS["genre"]),
        "tone": _metadata_text(setup.get("tone"), _STUDIO_SETUP_DEFAULTS["tone"]),
        "scope": _metadata_text(setup.get("scope"), _STUDIO_SETUP_DEFAULTS["scope"]),
        "production_goal": _metadata_text(
            setup.get("production_goal"),
            _STUDIO_SETUP_DEFAULTS["production_goal"],
        ),
        "selected_roles": _normalize_selected_role_keys(
            setup.get("selected_roles"),
            roles or _known_studio_roles(),
            reject_unknown=False,
            allowed_role_keys=allowed_role_keys,
        ),
    }
    metadata["setup"] = normalized_setup
    for key in _STUDIO_SETUP_DEFAULTS:
        metadata[key] = normalized_setup[key]
    return metadata


def _setup_context(metadata: Optional[dict]) -> str:
    setup = _safe_workspace_metadata(metadata).get("setup", {})
    labels = [
        ("platform", "Target platform"),
        ("genre", "Genre"),
        ("tone", "Tone"),
        ("scope", "Scope"),
        ("production_goal", "Production goal"),
    ]
    rows = [
        f"- {label}: {setup[key]}"
        for key, label in labels
        if setup.get(key) and setup.get(key) != _STUDIO_SETUP_DEFAULTS.get(key)
    ]
    if not rows:
        return ""
    return "\n\n## Studio Setup\n" + "\n".join(rows)


def _roles_for_payload(preset: dict, payload: StudioWorkspaceCreate) -> list[dict]:
    roles = list(preset["roles"])
    selected_keys = _normalize_selected_role_keys(
        payload.selected_roles,
        roles,
        reject_unknown=True,
    )
    if not selected_keys:
        return roles
    return [role for role in roles if role["key"] in selected_keys]


def _role_personality(role: dict, workspace_name: str, idea: str, metadata: Optional[dict] = None) -> str:
    context = _setup_context(metadata)
    return (
        f"You are the {role['name']} for the '{workspace_name}' studio workspace.\n"
        f"Mission: {role['mission']}\n\n"
        "Work as a senior production teammate. Be specific, ask for missing decisions, "
        "surface risks early, and keep outputs ready for implementation.\n\n"
        f"Current rough idea:\n{idea.strip()}{context}"
    )


def _artifact_markdown(kind: str, title: str, idea: str, workspace_name: str, metadata: Optional[dict] = None) -> str:
    idea = idea.strip()
    context = _setup_context(metadata)
    if kind == "vision_brief":
        return f"""# {workspace_name} Vision Brief

## Raw Idea
{idea}
{context}

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
{context}

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
{context}

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
{context}

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


def _initial_artifacts(idea: str, workspace_name: str, metadata: Optional[dict] = None) -> list[dict]:
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
            "content": _artifact_markdown(kind, title, idea, workspace_name, metadata),
        }
        for kind, title in specs
    ]


def _production_artifacts(idea: str, workspace_name: str, notes: str = "", metadata: Optional[dict] = None) -> list[dict]:
    context = f"\n\n## Producer Notes\n{notes.strip()}\n" if notes and notes.strip() else ""
    setup_context = _setup_context(metadata)
    return [
        {
            "kind": "product_requirements",
            "title": "Product Requirements",
            "content": f"""# {workspace_name} Product Requirements

## Source Idea
{idea.strip()}
{context}
{setup_context}
## Target Player
- Primary audience:
- Secondary audience:
- Player promise:

## MVP Definition
- Must ship:
- Should ship:
- Cut first:

## Success Metrics
- Prototype signal:
- Vertical slice signal:
- Launch signal:

## Constraints
- Team:
- Budget:
- Platform:
- Timeline:
""",
        },
        {
            "kind": "technical_architecture",
            "title": "Technical Architecture",
            "content": f"""# {workspace_name} Technical Architecture
{setup_context}

## Engine and Runtime
- Engine:
- Target platforms:
- Rendering assumptions:
- Save/data strategy:

## Core Systems
- Game state:
- Input:
- Level/content loading:
- Progression:
- Telemetry:

## Integration Risks
- Highest-risk dependency:
- Performance budget:
- Build/release path:

## Prototype Spike Plan
- Spike 1:
- Spike 2:
- Spike 3:
""",
        },
        {
            "kind": "vertical_slice_sprint",
            "title": "Vertical Slice Sprint",
            "content": f"""# {workspace_name} Vertical Slice Sprint
{setup_context}

## Sprint Goal
Prove the smallest version of the player fantasy with representative quality.

## Scope
- Playable loop:
- Representative content:
- UI feedback:
- Audio/art target:

## Exit Criteria
- Player can complete the loop without developer help.
- Performance is within the agreed budget.
- QA has a reproducible smoke suite.
- Team can estimate production scope from real evidence.
""",
        },
        {
            "kind": "risk_register",
            "title": "Risk Register",
            "content": f"""# {workspace_name} Risk Register
{setup_context}

| Risk | Owner | Signal | Mitigation | Status |
| --- | --- | --- | --- | --- |
| Core loop is not fun quickly enough | Game Designer | Playtest confusion or boredom | Build paper/prototype loop first | Open |
| Technical architecture blocks iteration | Tech Lead | Slow builds or brittle content | Spike risky systems before art/content scale | Open |
| Visual target exceeds capacity | Art Director | Asset list grows faster than schedule | Define style constraints and reuse rules | Open |
| QA finds late systemic issues | QA Lead | Bugs cluster around core state | Smoke tests before vertical slice | Open |
""",
        },
        {
            "kind": "qa_release_plan",
            "title": "QA Release Plan",
            "content": f"""# {workspace_name} QA Release Plan
{setup_context}

## Quality Bars
- Stability:
- Performance:
- Usability:
- Accessibility:

## Test Coverage
- Smoke tests:
- Core loop tests:
- Save/load tests:
- Platform tests:

## Release Gates
- No blocker bugs.
- Known issues are triaged.
- Core loop has passing smoke coverage.
- Producer signs off on ship scope.
""",
        },
    ]


def _production_work_items() -> list[dict]:
    return [
        {
            "role_key": "product_lead",
            "role_name": "Product Lead",
            "title": "Lock target player and promise",
            "description": "Turn the rough idea into a one-sentence player promise and primary audience.",
            "acceptance_criteria": "Target player, player promise, and non-goals are written in Product Requirements.",
            "priority": "high",
        },
        {
            "role_key": "game_designer",
            "role_name": "Game Designer",
            "title": "Define smallest playable loop",
            "description": "Specify the minimum sequence of player decisions needed to prove fun.",
            "acceptance_criteria": "Loop has input, challenge, feedback, reward, and failure/retry notes.",
            "priority": "high",
        },
        {
            "role_key": "tech_lead",
            "role_name": "Tech Lead",
            "title": "Write architecture spike list",
            "description": "Identify the riskiest technical unknowns before implementation starts.",
            "acceptance_criteria": "Technical Architecture contains three scoped spikes with expected signals.",
            "priority": "high",
        },
        {
            "role_key": "producer",
            "role_name": "Producer",
            "title": "Sequence vertical slice sprint",
            "description": "Turn the production pack into a first sprint with dependencies and checkpoints.",
            "acceptance_criteria": "Vertical Slice Sprint has goal, scope, owners, and exit criteria.",
            "priority": "high",
        },
        {
            "role_key": "art_director",
            "role_name": "Art Director",
            "title": "Define visual pillars",
            "description": "Describe the reusable style constraints and asset categories for the slice.",
            "acceptance_criteria": "Visual pillars fit the MVP scope and name what can remain placeholder.",
            "priority": "medium",
        },
        {
            "role_key": "narrative_designer",
            "role_name": "Narrative Designer",
            "title": "Set tone and world constraints",
            "description": "Define narrative boundaries that support gameplay without expanding scope.",
            "acceptance_criteria": "Tone, setting rules, and first-slice narrative beats are documented.",
            "priority": "medium",
        },
        {
            "role_key": "gameplay_engineer",
            "role_name": "Gameplay Engineer",
            "title": "Break prototype into build tickets",
            "description": "Translate the core loop into implementation-sized gameplay tasks.",
            "acceptance_criteria": "Each build ticket has a testable outcome and dependency note.",
            "priority": "high",
        },
        {
            "role_key": "qa_lead",
            "role_name": "QA Lead",
            "title": "Draft smoke test suite",
            "description": "Create the first quality gate for the prototype and vertical slice.",
            "acceptance_criteria": "QA Release Plan lists smoke cases, blocked states, and regression checks.",
            "priority": "medium",
        },
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
    work_items = db.query(StudioWorkspaceWorkItem).filter(
        StudioWorkspaceWorkItem.workspace_id == workspace.id,
    ).order_by(StudioWorkspaceWorkItem.sort_order.asc()).all()
    crew_ids = [a.crew_member_id for a in agents if a.crew_member_id]
    crew_by_id = {}
    if crew_ids:
        crew_by_id = {
            c.id: c
            for c in db.query(CrewMember).filter(CrewMember.id.in_(crew_ids)).all()
        }

    active_role_keys = {agent.role_key for agent in agents if agent.status == "active"}
    metadata = _safe_workspace_metadata(
        workspace,
        roles=_known_studio_roles() + _agent_roles(agents),
        allowed_role_keys=active_role_keys,
    )

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
        "metadata": metadata,
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
        "work_items": [
            {
                "id": item.id,
                "role_key": item.role_key,
                "role_name": item.role_name,
                "title": item.title,
                "description": item.description,
                "acceptance_criteria": item.acceptance_criteria,
                "priority": item.priority,
                "status": item.status,
                "sort_order": item.sort_order,
            }
            for item in work_items
        ],
    }


def _load_owned_workspace(db, workspace_id: str, owner: str) -> StudioWorkspace:
    workspace = db.query(StudioWorkspace).filter(StudioWorkspace.id == workspace_id).first()
    if not workspace or (owner and workspace.owner != owner):
        raise HTTPException(status_code=404, detail="Studio workspace not found")
    return workspace


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
        roles = _roles_for_payload(preset, payload)
        metadata = _metadata_from_payload(payload, [role["key"] for role in roles])
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
            workspace_metadata=json.dumps(metadata),
        )

        db = SessionLocal()
        try:
            db.add(workspace)
            for index, role in enumerate(roles):
                crew = CrewMember(
                    id=_new_id(),
                    owner=owner_for_rows,
                    name=f"{name} - {role['name']}",
                    avatar=role["avatar"],
                    user_name=None,
                    personality=_role_personality(role, name, idea, metadata),
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

            for index, artifact_spec in enumerate(_initial_artifacts(idea, name, metadata)):
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
            workspace = _load_owned_workspace(db, workspace_id, owner)
            return {"workspace": _workspace_to_dict(db, workspace)}
        finally:
            db.close()

    @router.post("/{workspace_id}/advance")
    async def advance_workspace(workspace_id: str, payload: StudioWorkspaceAdvance, request: Request):
        owner = require_user(request)
        db = SessionLocal()
        try:
            workspace = _load_owned_workspace(db, workspace_id, owner)
            owner_for_rows = workspace.owner
            active_agents = db.query(StudioWorkspaceAgent).filter(
                StudioWorkspaceAgent.workspace_id == workspace.id,
                StudioWorkspaceAgent.status == "active",
            ).all()
            active_role_keys = {row.role_key for row in active_agents}
            metadata = _safe_workspace_metadata(
                workspace,
                roles=_known_studio_roles() + _agent_roles(active_agents),
                allowed_role_keys=active_role_keys,
            )

            existing_kinds = {
                row.kind
                for row in db.query(StudioWorkspaceArtifact).filter(
                    StudioWorkspaceArtifact.workspace_id == workspace.id,
                ).all()
            }
            artifact_count = db.query(StudioWorkspaceArtifact).filter(
                StudioWorkspaceArtifact.workspace_id == workspace.id,
            ).count()
            for index, artifact_spec in enumerate(_production_artifacts(workspace.idea, workspace.name, payload.notes or "", metadata)):
                if artifact_spec["kind"] in existing_kinds:
                    continue
                doc = _create_document(
                    db,
                    owner=owner_for_rows,
                    title=f"{workspace.name} - {artifact_spec['title']}",
                    content=artifact_spec["content"],
                )
                db.add(StudioWorkspaceArtifact(
                    id=_new_id(),
                    workspace_id=workspace.id,
                    document_id=doc.id,
                    owner=owner_for_rows,
                    kind=artifact_spec["kind"],
                    title=artifact_spec["title"],
                    sort_order=artifact_count + index,
                    status="draft",
                ))

            existing_work_items = db.query(StudioWorkspaceWorkItem).filter(
                StudioWorkspaceWorkItem.workspace_id == workspace.id,
            ).count()
            if existing_work_items == 0:
                items = [
                    item for item in _production_work_items()
                    if not active_role_keys or item["role_key"] in active_role_keys
                ]
                for index, item in enumerate(items):
                    db.add(StudioWorkspaceWorkItem(
                        id=_new_id(),
                        workspace_id=workspace.id,
                        owner=owner_for_rows,
                        role_key=item["role_key"],
                        role_name=item["role_name"],
                        title=item["title"],
                        description=item["description"],
                        acceptance_criteria=item["acceptance_criteria"],
                        priority=item["priority"],
                        status="todo",
                        sort_order=index,
                    ))

            workspace.status = "active"
            workspace.phase = "production_planning"
            workspace.summary = "Production planning pack and role-owned board are ready."
            workspace.current_focus = "Drive the vertical slice from role tasks to implementation-ready tickets."
            metadata["production_pack_version"] = 1
            if payload.notes and payload.notes.strip():
                metadata["latest_advance_notes"] = payload.notes.strip()
            workspace.workspace_metadata = json.dumps(metadata)

            db.commit()
            return {"workspace": _workspace_to_dict(db, workspace)}
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to advance studio workspace: {exc}")
        finally:
            db.close()

    @router.patch("/{workspace_id}/work-items/{item_id}")
    async def update_work_item(workspace_id: str, item_id: str, payload: StudioWorkItemUpdate, request: Request):
        owner = require_user(request)
        valid_statuses = {"todo", "doing", "blocked", "done"}
        valid_priorities = {"low", "medium", "high"}
        db = SessionLocal()
        try:
            workspace = _load_owned_workspace(db, workspace_id, owner)
            item = db.query(StudioWorkspaceWorkItem).filter(
                StudioWorkspaceWorkItem.id == item_id,
                StudioWorkspaceWorkItem.workspace_id == workspace.id,
            ).first()
            if not item:
                raise HTTPException(status_code=404, detail="Studio work item not found")
            if payload.status is not None:
                status = payload.status.strip().lower()
                if status not in valid_statuses:
                    raise HTTPException(status_code=400, detail="Invalid work item status")
                item.status = status
            if payload.priority is not None:
                priority = payload.priority.strip().lower()
                if priority not in valid_priorities:
                    raise HTTPException(status_code=400, detail="Invalid work item priority")
                item.priority = priority

            db.commit()
            return {"workspace": _workspace_to_dict(db, workspace)}
        except HTTPException:
            db.rollback()
            raise
        finally:
            db.close()

    return router
