# Things the team should know but probably doesn't

This is a senior-engineer orientation memo, not a complete audit. It focuses on architecture surprises, hidden dependencies, places where the code looks safer than it is, and corners that carry more operational risk than their file names suggest.

One important caveat: the local git history in this checkout only goes back far enough to show recent activity around late May and June 2026, so "dark corner" below means low-obviousness plus high blast radius, not literally "untouched for months" unless the source itself says so.

## Fast orientation

Odysseus is not a small FastAPI backend with a static frontend. It is a monolith that starts an API server, a browser-hosted app, an agent runtime, an MCP client/server layer, background pollers, schedulers, model-serving helpers, Chroma/RAG plumbing, email automation, and a pile of stateful local files.

The API app is created in `app.py:109`, mounts static assets at `app.py:441`, initializes YouTube and RAG facilities at `app.py:486` and `app.py:491`, wires core managers at `app.py:517`, and then includes dozens of routers between `app.py:572` and `app.py:788`. Treat `app.py` as the system composition root, not as a thin web entry point.

The real product state lives under `data/`, not just in the database. `src/constants.py:10` makes `DATA_DIR` default to `<repo>/data` unless `ODYSSEUS_DATA_DIR` is set, and `src/constants.py:16` through `src/constants.py:38` define JSON and database files there. `.gitignore:27` through `.gitignore:35` intentionally hide that state from git. Moving, deleting, or mounting `data/` differently changes auth, settings, sessions, uploads, vector DB state, generated images, and more.

The repo has compatibility layers from refactors. `core/constants.py:1` through `core/constants.py:11` re-export `src.constants`; `src/search/core.py:1` through `src/search/core.py:12` re-export `services.search.core`; `src/search/ranking.py:1` through `src/search/ranking.py:14` re-export `services.search.ranking`; `src/youtube_handler.py:1` through `src/youtube_handler.py:23` is a wrapper around `services/youtube/youtube_handler.py`. When changing behavior, first find the canonical module.

## Architecture surprises

### Startup is a scheduler, migrator, warmer, and background worker launcher

Startup does a lot more than bring up HTTP. `app.py:911` through `app.py:1148` purges incognito sessions, starts the background monitor, starts MCP services, warms tool indexes and endpoints, ensures default scheduled tasks, backfills skill owners, starts the scheduler, starts an hourly null-owner sweep, starts a nightly skill audit, and starts cookbook serving lifecycle tasks.

Several long-running tasks are deliberately retained in `_startup_tasks` at `app.py:932` through `app.py:935`, with more added later for endpoint keepalive at `app.py:1002` through `app.py:1012`, null-owner cleanup at `app.py:1094` through `app.py:1107`, skill audit at `app.py:1109` through `app.py:1137`, and cookbook serving at `app.py:1139` through `app.py:1146`. Shutdown at `app.py:1150` through `app.py:1173` stops a few named systems, but it does not explicitly cancel every task retained in `_startup_tasks`. The event loop will normally clean those up during process shutdown, but hot reloads and embedded test runs can leave behavior that is hard to reason about.

The lifespan function is assigned after app creation at `app.py:900` through `app.py:908` with `app.router.lifespan_context = _lifespan`. That is valid, but it is easy to miss if someone searches only for a `FastAPI(lifespan=...)` constructor.

### Sessions are DB-backed, but parts of the API still look file-backed

`core/session_manager.py:61` through `core/session_manager.py:64` keeps `sessions_file` only for backward compatibility and says it is no longer used. `save_sessions()` is a no-op at `core/session_manager.py:621` through `core/session_manager.py:622`.

At the same time, `core/session_manager.py:70` through `core/session_manager.py:82` only preloads recent active sessions with messages into an in-memory cache. `get_sessions_for_user()` returns from that in-memory cache at `core/session_manager.py:612` through `core/session_manager.py:619`. Code that assumes this is a complete database query can silently miss older or cold sessions.

### The agent has three different execution worlds

Agent subprocess tools default to `DATA_DIR`, not the repository root. `src/tool_execution.py:29` through `src/tool_execution.py:34` define the agent subprocess working directory, and `src/tool_execution.py:260` through `src/tool_execution.py:263` expose that path.

The app shell routes run from the OS home directory instead. `_exec_shell()` uses `Path.home()` at `routes/shell_routes.py:430` through `routes/shell_routes.py:439`, and PTY sessions do the same at `routes/shell_routes.py:477` through `routes/shell_routes.py:483`.

Background shell jobs are a third path. `src/tool_execution.py:703` through `src/tool_execution.py:725` recognizes `#!bg` bash blocks, while `src/bg_jobs.py:81` through `src/bg_jobs.py:118` writes a script and launches it detached. The monitor in `src/bg_monitor.py:75` through `src/bg_monitor.py:132` can append an assistant continuation after a background job finishes, and it polls every five seconds at `src/bg_monitor.py:135` through `src/bg_monitor.py:157`. In other words, background job completion can trigger a headless LLM continuation outside the original live request.

### Research has a real duplicate, not just a shim

Most duplicated-looking modules are compatibility wrappers, but research is different. The app initializer imports `ResearchHandler` from `src.research_handler` at `src/app_initializer.py:22` and constructs it at `src/app_initializer.py:83`. That implementation protects research JSON paths with `_research_json_path()` at `src/research_handler.py:53` through `src/research_handler.py:62`.

There is also `services/research/research_handler.py`, which starts with a stale `# src/research_handler.py` comment at `services/research/research_handler.py:1` and defines its own `ResearchHandler` around `services/research/research_handler.py:24` through `services/research/research_handler.py:32`. `services/research/service.py:8` imports that duplicate and `services/research/service.py:35` through `services/research/service.py:47` constructs it. This is a genuine drift risk because fixes in one handler may not land in the other.

### The threat model is useful, but partly stale

`THREAT_MODEL.md:71` through `THREAT_MODEL.md:81` lists known gaps including no filesystem sandbox and partial search consolidation. The live code has moved since then: `src/tool_execution.py:39` through `src/tool_execution.py:54` documents path confinement, `src/tool_execution.py:182` through `src/tool_execution.py:213` implements workspace resolution, and `src/tool_execution.py:236` through `src/tool_execution.py:257` rejects sensitive roots. The `src/search/*` modules inspected in this pass are shims to `services.search.*`, not independent copies.

Do not delete the threat model. Do treat it as a living document that needs reconciliation with code before being used as an incident-response checklist.

## Hidden dependencies

### Chroma and FastEmbed are load-bearing even when the app degrades gracefully

RAG startup is deliberately lazy and degraded-state tolerant. `app.py:491` through `app.py:509` describes lazy Chroma initialization so startup can continue when the dependency is unavailable. `src/app_initializer.py:56` through `src/app_initializer.py:75` similarly lets `MemoryVectorStore` fail without aborting the whole app.

That tolerance hides product impact. Tool selection in `src/agent_loop.py:1885` through `src/agent_loop.py:1910` depends on RAG and low-signal logic. Owner-isolation regressions have existed specifically in RAG fallback behavior: `tests/test_rag_keyword_fallback_owner.py:1` through `tests/test_rag_keyword_fallback_owner.py:10` documents a prior leak where ownerless docs appeared when Chroma failed and keyword fallback ran.

### Docker Compose is part of the app architecture

`docker-compose.yml:1` through `docker-compose.yml:22` defines app mounts for data, logs, SSH config, Hugging Face cache, and local files. It also wires SearXNG, Chroma, SQLite path, and in-process poller/task flags through environment variables at `docker-compose.yml:31` through `docker-compose.yml:48`.

SearXNG is pinned for a reason. `docker-compose.yml:83` through `docker-compose.yml:89` explains that newer tags changed health-check behavior. Chroma is a separate service at `docker-compose.yml:73` through `docker-compose.yml:81`, and ntfy is included at `docker-compose.yml:133` through `docker-compose.yml:142`. A "backend only" deployment can look healthy while search, vector memory, or notifications are degraded.

### Email automation spans routes, pollers, MCP, settings, and SQLite

Email is not contained in `routes/email_routes.py`. The poller module declares its design at `routes/email_pollers.py:1` through `routes/email_pollers.py:16`. A legacy auto-summarize poller is still present for backward compatibility at `routes/email_pollers.py:970` through `routes/email_pollers.py:977`. Scheduled sending polls SQLite directly at `routes/email_pollers.py:981` through `routes/email_pollers.py:1072`, loops every 30 seconds at `routes/email_pollers.py:1074` through `routes/email_pollers.py:1087`, and is gated by `ODYSSEUS_INPROCESS_POLLERS` at `routes/email_pollers.py:1092` through `routes/email_pollers.py:1100`.

The route layer updates email settings and encrypted credentials at `routes/email_routes.py:3004` through `routes/email_routes.py:3059`. The MCP email server has its own account-loading path and fallback behavior: `mcp_servers/email_server.py:52` through `mcp_servers/email_server.py:55` says it is multi-account aware but falls back to flat env/settings, and `mcp_servers/email_server.py:190` through `mcp_servers/email_server.py:199` caches account resolution by account selector, not by owner. Audit this before assuming MCP email operations have the same owner isolation as HTTP routes.

The MCP server also has schema knowledge for scheduled emails. `_stash_agent_draft()` touches `scheduled_emails` schema directly at `mcp_servers/email_server.py:900` through `mcp_servers/email_server.py:940` because the MCP server can boot independently. That is a hidden migration dependency outside `core/database.py`.

### CI is informational in places, not an authoritative gate

The syntax job uses Python 3.11 at `.github/workflows/ci.yml:25` through `.github/workflows/ci.yml:29`. The pytest smoke job is explicitly `continue-on-error: true` at `.github/workflows/ci.yml:49` through `.github/workflows/ci.yml:55` because tests can be flaky or environment-dependent. A later job installs requirements and runs pytest at `.github/workflows/ci.yml:84` through `.github/workflows/ci.yml:93`, also on Python 3.11.

Docker uses `python:3.14-slim` at `Dockerfile:1`. Dependencies are mostly unpinned in `requirements.txt:1` through `requirements.txt:49`. This means a green-ish CI run does not prove the Docker runtime dependency set is stable.

## Things that look correct but aren't

### `owner=None` rarely means "safe default"

There are several meanings of "no owner" in the codebase. `src/auth_helpers.py:62` through `src/auth_helpers.py:109` returns an empty user string when auth is disabled, localhost bypass applies, or first-run loopback is allowed. `src/auth_helpers.py:140` through `src/auth_helpers.py:148` makes `owner_filter()` a no-op for falsey users and includes null-owner shared rows by default.

Database helper comments are explicit about the danger. `core/database.py:2270` through `core/database.py:2276` says `get_upcoming_events(owner=None)` is intentionally unscoped and multi-user callers must pass a username.

There is a migration/sweep system to assign legacy null-owner data to the first admin. `_migrate_assign_legacy_owner()` starts at `core/database.py:1186`, has a hand-maintained table allowlist at `core/database.py:1232` through `core/database.py:1239`, and rewrites `memory.json` at `core/database.py:1259` through `core/database.py:1275`. `app.py:1094` through `app.py:1107` also starts an hourly null-owner sweep. Any new owner-bearing table needs explicit migration and filtering work; otherwise rows can become shared by accident.

The tests know this is a real risk. `tests/test_research_endpoint_owner_scope.py:1` through `tests/test_research_endpoint_owner_scope.py:12` documents endpoint owner-scope regressions that could expose internal URLs or API keys. `tests/test_rag_keyword_fallback_owner.py:1` through `tests/test_rag_keyword_fallback_owner.py:10` documents the RAG fallback leak class.

### Endpoint URLs are not identities

Session creation resolves endpoint IDs and owner-filters `ModelEndpoint` at `routes/session_routes.py:319` through `routes/session_routes.py:354`. It also rejects raw endpoint URLs for non-admins in multi-user mode at `routes/session_routes.py:140` through `routes/session_routes.py:155`.

The compare route explains why URL matching is dangerous. `_owned_endpoint_by_url()` notes that URL-only matching can copy a decrypted API key at `routes/compare_routes.py:22` through `routes/compare_routes.py:39`, and the ID-pinned helper exists to avoid wrong-key matches at `routes/compare_routes.py:42` through `routes/compare_routes.py:57`. Do not add a new model-calling path that accepts arbitrary base URLs and later tries to "find" credentials by URL.

### Secret encryption is not a magic boundary

`src/secret_storage.py:1` through `src/secret_storage.py:19` says the Fernet key is stored on disk at `data/.app_key`, protects against casual SQLite exfiltration, and does not protect against a live process compromise. `core/database.py:58` through `core/database.py:80` uses `EncryptedText` that passes legacy plaintext through until rewritten.

`src/secret_storage.py:68` through `src/secret_storage.py:83` returns an empty string on decryption failure. That keeps the UI from crashing, but it can turn a key mismatch into "credential silently unconfigured" instead of an obvious boot-time failure.

### Localhost bypass is broader than it first appears

Auth is wired in `app.py:190` through `app.py:199`, with explicit `LOCALHOST_BYPASS` handling. Middleware exemptions are defined at `app.py:200` through `app.py:233`, and trusted loopback detection intentionally ignores forwarded headers at `app.py:275` through `app.py:299`.

The shell route accepts either admin users or the internal tool identity at `routes/shell_routes.py:47` through `routes/shell_routes.py:64`, while also rejecting cross-site browser fetches at `routes/shell_routes.py:66` through `routes/shell_routes.py:69`. This is consistent with the private-network threat model, but it means proxying and local exposure decisions are security-critical.

### Generated image access has a permissive failure path

Generated image serving checks ownership, but on exception it falls through. The route starts at `app.py:444`, validates ownership around `app.py:448` through `app.py:465`, and then `pass`es on exceptions at `app.py:467` through `app.py:468` before serving from `data/generated_images`. That may be intentional compatibility behavior, but it is the sort of "looks guarded" code path that deserves a deliberate decision.

## Dark corners by blast radius

### The frontend is mostly hand-maintained large files

`static/style.css:1` through `static/style.css:3` says the stylesheet is consolidated, and the file is about 36,831 lines long. The largest frontend files include `static/js/document.js`, `static/js/emailLibrary.js`, `static/js/slashCommands.js`, `static/js/settings.js`, `static/js/notes.js`, `static/js/chat.js`, and `static/app.js`. `ROADMAP.md:48` through `ROADMAP.md:54` explicitly lists CSS cleanup, tour helper cleanup, modal/window positioning, mobile media overrides, and dead code removal as refactor targets.

This is not inherently bad, but it means frontend changes need visual regression discipline. There is no ordinary npm build pipeline in `package.json:1` through `package.json:11`; the frontend is mostly shipped as static assets.

### Tool policy and agent prompting are central security surfaces

The agent prompt and tool loop are not just UX. `src/agent_loop.py:60` through `src/agent_loop.py:89` defines core system behavior and fenced tool-block execution rules. `src/agent_loop.py:140` through `src/agent_loop.py:156` contains cookbook-specific warnings about not using raw bash or tmux for model serving.

The policy layer says its rules are guide-only at `src/tool_policy.py:11` through `src/tool_policy.py:17`. It builds deny/hide behavior at `src/tool_policy.py:174` through `src/tool_policy.py:203` when users ask for no tools. Prompt-injection handling depends on `src/prompt_security.py:8` through `src/prompt_security.py:23` and `src/prompt_security.py:60` through `src/prompt_security.py:82`. Changes here need tests and a threat-model update, not just prompt edits.

### File editing tools are simple string/file operations

The filesystem tool layer has confinement now, but the editing semantics are still simple. `src/agent_tools/filesystem_tools.py:47` through `src/agent_tools/filesystem_tools.py:105` performs plain string replace and writes the result directly. `src/agent_tools/filesystem_tools.py:157` through `src/agent_tools/filesystem_tools.py:190` writes files directly.

That is adequate for a local tool, but it is not an atomic patch engine. Race conditions, encoding surprises, and ambiguous repeated text are application-level concerns.

### Database migrations are hand-coded

`core/database.py:1750` through `core/database.py:1785` runs initialization and many manual migration helpers. There is no Alembic-style migration ledger in the files inspected. Schema evolution relies on idempotent Python helpers, comments, and tests.

The owner migration is especially sensitive because its table list is hand-maintained at `core/database.py:1232` through `core/database.py:1239`. A new table with `owner` semantics can compile and run while quietly skipping legacy assignment.

### Tests document production risks, but CI may not enforce them

`tests/TESTING_STANDARD.md:21` through `tests/TESTING_STANDARD.md:29` lays out an ambitious test goal, and `tests/TESTING_STANDARD.md:78` through `tests/TESTING_STANDARD.md:86` defines a fast lane. The existence of focused regression tests around owner scoping is a good sign.

But the CI workflow still treats at least one pytest lane as informational at `.github/workflows/ci.yml:49` through `.github/workflows/ci.yml:55`. A failing owner-scope or degraded-mode test can be easy to overlook if reviewers treat the check suite as binary green/red.

## Practical guardrails for future changes

1. When adding any user-owned resource, add the schema column, owner filter, legacy owner migration or sweep behavior, and at least one regression test where another user cannot see it. Start from `src/auth_helpers.py:140` through `src/auth_helpers.py:148`, `core/database.py:1186` through `core/database.py:1275`, and the tests under `tests/test_*owner*.py`.

2. When adding model or endpoint behavior, prefer endpoint IDs over URLs and reuse the existing owner resolution paths. Read `routes/session_routes.py:319` through `routes/session_routes.py:354` and `routes/compare_routes.py:42` through `routes/compare_routes.py:57` first.

3. When adding background behavior, decide whether it belongs to startup, the scheduler, a poller, an MCP server, or a detached background job. The app already has all five patterns.

4. When handling untrusted external text, keep it wrapped as untrusted context. The relevant primitives are in `src/prompt_security.py:26` through `src/prompt_security.py:41` and `src/prompt_security.py:60` through `src/prompt_security.py:82`.

5. When changing email, audit HTTP routes, pollers, MCP server behavior, settings JSON, and SQLite schema together. The hidden coupling is real.

6. When changing startup or shutdown, search for `_startup_tasks`, scheduler start/stop, MCP lifecycle, cookbook lifecycle, and poller flags. It is easy to start a forever loop without giving it a symmetrical shutdown path.

7. When relying on tests, run the targeted tests locally and read the workflow result carefully. CI contains useful checks, but parts of it are explicitly non-blocking.

