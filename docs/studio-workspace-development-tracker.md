# Studio Workspace Development Tracker

## Current Stage

**Phase:** Production Alpha

The Studio Workspace has moved beyond MVP. It can create a scoped AI product team, seed editable planning artifacts, advance into production planning, and maintain a role-owned work board. The current work is focused on making studio creation configurable and keeping the feature isolated from the core chat/document/workspace flows.

## Completed

- Added Studio Workspace database models for workspaces, agents, artifacts, and work items.
- Added `/api/studio-workspaces` routes for listing, creating, reading, advancing, and updating board items.
- Reused existing `CrewMember` rows for studio roles.
- Reused existing `Document` and `DocumentVersion` rows for generated artifacts.
- Added a Studio button in the existing overflow tools menu.
- Added the Studio modal with Overview, Board, and Artifacts tabs.
- Added an Advance action that creates a production pack and role-owned board.
- Added WSL launch scripts that keep Odysseus running after the Windows launcher closes.
- Moved Studio creation out of the main Studio surface into a dedicated setup popup.
- Added setup customization for platform, genre, tone, scope, production goal, and selected roles.
- Persisted setup customization in `StudioWorkspace.workspace_metadata`.
- Threaded setup customization into role prompts and seeded planning documents.

## Active Work

- Make the setup popup feel native to Odysseus without leaking styles into the rest of the app.
- Keep Studio-specific code scoped to:
  - `routes/studio_workspace_routes.py`
  - `static/js/studioWorkspace.js`
  - `.studio-*` CSS selectors in `static/style.css`
  - Studio-only database models in `core/database.py`
- Preserve backwards compatibility for studios created before setup customization existed.

## Next Milestones

1. **Agent Execution Loop**
   - Let selected studio roles produce structured proposals, critiques, and updates.
   - Persist agent turns or decisions separately from chat messages.

2. **Artifact Generation Pipeline**
   - Move from deterministic templates to model-assisted artifact expansion.
   - Keep outputs editable as normal documents.
   - Add version summaries that explain which role produced each change.

3. **Board-to-Implementation Bridge**
   - Convert work items into implementation tasks.
   - Link work items to documents, files, commits, or background jobs.
   - Add status history and ownership metadata.

4. **Studio Isolation Hardening**
   - Confirm every Studio route is owner-scoped.
   - Keep Studio-specific UI out of core app initialization beyond one import and one toolbar binding.
   - Add tests for legacy metadata, selected-role boards, and invalid setup input.

5. **Production UX**
   - Add edit actions for setup metadata after creation.
   - Add duplicate/archive/delete Studio actions.
   - Add better empty states and per-role activity indicators.

## Known Gaps

- Advance currently creates deterministic production artifacts; it does not yet ask the agents to deliberate.
- Work items can move status/priority, but they do not yet have comments, history, owners beyond role labels, or linked implementation output.
- Studio setup metadata is persisted as JSON text rather than a normalized table.
- Existing created studios will not automatically gain new setup metadata unless edited later.
- The Studio still depends on the global document editor for artifact editing.

## Verification

Last focused verification:

- `python -m py_compile routes/studio_workspace_routes.py core/database.py src/database.py`
- `node --check static/js/studioWorkspace.js`
- `venv/bin/python -m pytest tests/test_studio_workspace_routes.py` inside WSL
- `git diff --check`

## Current Decision Log

- **Studio is not the filesystem Workspace.** The filesystem feature remains `/api/workspace` and `static/js/workspace.js`; Studio uses `/api/studio-workspaces` and `static/js/studioWorkspace.js`.
- **Studio roles are CrewMembers.** This keeps role identity compatible with the rest of Odysseus instead of inventing a parallel persona system.
- **Studio artifacts are Documents.** Generated specs remain editable, versioned documents.
- **Studio CSS must stay scoped.** New styles should use `.studio-*` selectors and existing app tokens.
- **Creation belongs in a popup.** The main Studio surface is for managing and advancing studios, not for carrying a permanent creation form.
