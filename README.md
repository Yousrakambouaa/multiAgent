# Telegram Unity Studio

A persistent, budgeted supervisor turns an approved `/create <description>` plan into an isolated Unity prototype. Planning uses a versioned specification for any Unity game genre, 2D/3D/mixed scenes, explicitly requested components, multiple scripts and runtime probes. Missing optional details use sensible defaults; they do not block planning. Version 1 saved platformer plans remain readable and keep their original build path. The original Unity project in `generated_game` supplies settings and packages for new projects; keep it alongside the application.

## Launch

From `C:\Users\new\Desktop\multiAgent`:

```powershell
.\.venv\Scripts\python.exe telegram_bot.py
```

Keep one controller running. An OS lock prevents two new bot/CLI controllers from running together; Telegram polling conflicts also stop the duplicate. Stop an older bot before launching the new version.

The existing `.env` supplies `TELEGRAM_BOT_TOKEN`, numeric `TELEGRAM_ALLOWED_USER_ID`, and `OPENROUTER_API_KEY`. See `.env.example` for optional configuration. No existing credential is replaced. A fresh environment can install `requirements.txt`.

## Unity projects in GitHub

The repository includes the base project in `generated_game/` and generated Unity source projects in `jobs/<id>/UnityProject/`. See `jobs/README.md` for the generation status of the included projects; partial projects are retained as source and are not presented as verified games.

Each Unity project tracks `Assets` (including `.meta` files), `Packages`, and `ProjectSettings`. Open its folder in Unity Hub using the version in `ProjectSettings/ProjectVersion.txt`. Unity rebuilds its local `Library` and other caches when opened.

Credentials, Telegram/job records, cost ledgers, model caches, Unity caches, ZIP exports, and compiled APKs are excluded from Git. Fresh clones start without an active job; create `.env` from `.env.example` before launching the bot. Generated projects remain directly usable in Unity independently of the studio job history. New generated Unity source folders use the same Git rules.

## Telegram workflow

1. `/create A woodland platformer with jumping, coins and patrolling enemies` creates a game plan without asking follow-up questions.
2. `/plan` inspects the plan; `/budget` inspects charges and reservations. Send `/approve` to start Unity generation, or `/cancel` to discard the plan.
3. `/status` follows the build. Additional instructions during planning are added to the prompt while preserving the original. Instructions sent while a plan waits for `/approve` are included when approved. Instructions received after the build starts are saved and reported as queued, not applied to that build.
4. `/result` retrieves the source archive, validation report, instructions, captured preview and NUnit results. Verified artifacts are also sent automatically after a successful build. An APK is included only after a successful Android build and archive integrity check.

`/start` introduces the bot; `/myid` reports the sender's own Telegram ID. Every command that reads or changes jobs checks the configured authorized user ID. Plain game requests use the same workflow. `tt` is rejected without an API call. Unrelated ordinary messages are rejected; `/create` treats its description as the authoritative game request. The plan records prompt requirements, assumptions, tasks and known limitations before `/approve`.

## CLI

```powershell
# Same supervisor, including real planning, under the default $2 cap
.\.venv\Scripts\python.exe main.py create "Create a woodland platformer with coins" --approve
.\.venv\Scripts\python.exe main.py plan
# Optional manual retry for an older saved job
.\.venv\Scripts\python.exe main.py approve

# Deterministic example, planned and built
.\.venv\Scripts\python.exe main.py demo --approve

# Reverify saved artifacts without repeating paid agents; optionally build Android
.\.venv\Scripts\python.exe main.py verify --android

.\.venv\Scripts\python.exe main.py models
.\.venv\Scripts\python.exe main.py status
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# Build Android from an unchanged project that has already passed verification
.\.venv\Scripts\python.exe -m tools.build_android
```

CLI `--android` enables the Android development build. For Telegram, set `STUDIO_BUILD_ANDROID=1` before starting the bot. Unity's Android SDK, NDK and JDK must be installed. The APK is for development; signing for publication and installation/device testing are separate, unverified steps.

## Optional Unity MCP

The existing Unity CLI remains the normal compile, scene-build, test and APK path. MCP is disabled by default. To enable the optional connector, install **MCP for Unity** in each job project you intend to inspect (`jobs/<id>/UnityProject`): in Unity, open Package Manager and add `https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#v10.3.0`. Open that same project with Unity 2021.3 or newer, choose **Window → MCP for Unity**, and start its bridge. The current configured Unity version is Unity 6.

The connector starts the matching Python server over stdio with this command (it is launched automatically by the app; do not start a second copy):

```powershell
uvx --python .\.venv\Scripts\python.exe --from mcpforunityserver==10.3.0 mcp-for-unity
```

`uv`/`uvx` and Python 3.10+ must be installed. Set these optional values in `.env`:

The app automatically supplies its own Python interpreter to `uvx` unless `--python` is explicitly configured. This avoids selecting an incompatible system Python (Python 3.15 caused a dependency build failure during the delivery audit).

```dotenv
STUDIO_UNITY_MCP_ENABLED=1
STUDIO_UNITY_MCP_COMMAND=uvx
STUDIO_UNITY_MCP_ARGS_JSON='["--from","mcpforunityserver==10.3.0","mcp-for-unity"]'
STUDIO_UNITY_MCP_TIMEOUT=20
```

Stop the Telegram bot first because the CLI and bot share a single-controller lock. With Unity open on the current job's project, check tool discovery and exercise the disposable scene create/inspect/cleanup path:

```powershell
.\.venv\Scripts\python.exe main.py mcp-status
.\.venv\Scripts\python.exe main.py mcp-smoke
```

Then start the Telegram application normally:

```powershell
.\.venv\Scripts\python.exe telegram_bot.py
```

The app discovers the server's live tool list and exposes role-limited Python tool facades to the five existing roles. Supervisor and Game Designer can inspect; Unity Developer can create scenes/GameObjects/components and search/create/import project assets; Art Designer can search/create/import assets; QA can inspect scenes, read the console, validate scripts and request tests. The wrapper excludes arbitrary code/menu execution and confines asset paths to the target project's `Assets` folder. Tool calls are made by trusted orchestration, so game-prompt text is not a command or raw tool argument. The current CrewAI and AutoGen model adapters remain on their existing metered JSON path; they do not advertise model-selected function calling.

When enabled and a matching Editor is available, the existing agents now request role-specific observations through `ModelService`: scene hierarchy for Supervisor/Designer/Developer, asset search for Art Designer, and console errors for QA. Designer/Developer/QA observations are included as data in their metered model requests, including CrewAI and AutoGen calls. Successful use records `mcp_tool_used`; failures record `mcp_fallback` and retain the existing workflow. This does not start an Editor automatically; the batch Unity builder requires that project to be closed in interactive Unity.

`mcp-smoke` creates a disposable scene and cube, adds a Rigidbody, creates a temporary material, checks the scene hierarchy/GameObject and reads console errors, then restores the original scene and removes its temporary assets. It refuses to run if the original scene is dirty, multiple scenes are loaded, or the probe paths/names already exist.

During normal generation, the current deterministic Unity CLI assembler and its evidence stay authoritative. When MCP is enabled and the matching Editor is reachable after assembly, the pipeline adds a read-only QA scene inspection; connection failures are logged and the CLI result remains unchanged. `mcp-smoke` is the explicit MCP scene-creation check. Installing the Unity package is project-specific; it is not injected into `generated_game` or existing game folders automatically.

## Architecture

| Module | Responsibility |
| --- | --- |
| `telegram_bot.py` | Authorization, commands, background operations, progress and artifact delivery |
| `studio/supervisor.py` | Task graph, plan approval, persistent state, specialist handoffs, bounded repair loop |
| `studio/schemas.py`, `studio/flexible.py` | Version 1 platformer compatibility and version 2 genre-neutral GameSpec, assets, tasks, scene/component contracts, probes, trace and evidence |
| `studio/registry.py`, `studio_models.json` | Live model availability/capability/pricing lookup; explicit primary/fallback allowlists |
| `studio/api.py`, `studio/costs.py` | Shared OpenRouter gateway, pre-call reservations, reported charges, conservative unknown-charge estimates |
| `studio/agents.py`, `studio/frameworks.py` | Game Designer, Unity Developer and QA/Debugger; metered CrewAI and AutoGen adapters |
| `studio/assets.py` | Real Image API calls, PNG validation/resizing, alpha checks, cache, provenance and original procedural fallback |
| `studio/scenes.py` | Deterministic Scene Architect compilation from the shared game/UI/art specifications |
| `studio/templates/Runtime` | Complete reusable platformer physics, input, scoring, enemies, camera, menus and touch controls |
| `studio/templates/Editor` | General scene/component/property/reference assembly, sprite import, prefabs, build settings and Android build |
| `studio/templates/Tests` | Real Unity EditMode and PlayMode suites |
| `studio/unity.py` | Fixed Unity CLI invocations, cancellation/timeouts, fresh evidence and NUnit parsing |
| `studio/mcp_client.py` | Optional MCP stdio client, live tool discovery, five-role allowlists, Unity project/path checks and disposable smoke test |
| `studio/artifacts.py` | Source packaging, reports, artifact hashes and verified-APK filtering |
| `studio/verification.py` | Local re-verification without additional model calls |
| `studio/generation.py` | Version 2 job execution, bounded script/asset validation and requirement reports |
| `studio/templates/Editor/UniversalSceneBuilder.cs` | Explicit 2D/3D/mixed scene construction with supplied components, meshes, cameras, lighting, prefabs and materials |
| `studio/templates/Tests/PlayMode/PromptRuntimeTests.cs` | Runs plan-linked control and gameplay probes and writes fresh runtime evidence |

The studio has exactly five agent identities, registered in `Supervisor.agents`:

1. **Supervisor** coordinates tasks, model selection, spending, progress and delivery.
2. **Game Designer** plans gameplay, levels, mechanics and UI requirements, including `UIPlan`.
3. **Unity Developer** generates scripts, assembles scenes/UI/components using tools, and repairs scene specifications.
4. **Art Designer** generates sprites, textures, backgrounds and other visual assets.
5. **QA & Debugger** handles validation findings, compiler/runtime diagnostics and code repairs.

UI Designer and Scene Architect are no longer separate agents. The existing `ui` and `scene` keys in `studio_models.json` remain model task profiles owned by Game Designer and Unity Developer respectively; their model IDs, fallbacks and request limits are unchanged. `AGENT_MODEL_PROFILES` documents this ownership. Compilation, scene building, testing, packaging and APK building remain deterministic tools. The existing `service.json`/`service.post` boundary remains available for a future provider adapter; no new external API is integrated.

Default planning uses CrewAI with a custom metered LLM. Default QA repair uses AutoGen with a custom metered model client. They cannot access API credentials or bypass the shared ledger. `STUDIO_DESIGNER_BACKEND=direct` and `STUDIO_QA_BACKEND=direct` select the simpler structured handoff implementation. There are no free-form specialist conversations or unrestricted shell tools.

New version 2 jobs generate multiple named C# components and explicit scene descriptions. The builder does not inject a player, camera, floor, coins, menu or particular physics system. A scene must specify its camera, lighting for 3D, gameplay objects, components and references. Tests load each named scene, execute key/invoke/click steps, check runtime state and save machine-readable evidence linked to requirement IDs. Existing version 1 platformer jobs continue using their maintained runtime and deterministic scene builder. Generated C# is scope-checked and still must compile and pass Unity tests; this is not a security sandbox for malicious code.

Each job lives in `jobs/<id>/`, including a self-contained `UnityProject`, specifications, append-only event log, cost ledger, generated files, Unity logs and validation evidence. `jobs/current.txt` records the current job. Plans wait for `/approve`; a job interrupted after approval resumes on bot startup. Its ledger remains charged and generated images can reuse cache. In-flight calls with unknown billing retain their reservations. A global Unity-job lock prevents overlapping builds, in addition to isolated projects.

## Budget and model policy

`STUDIO_JOB_BUDGET_USD` defaults to 2. Every paid attempt reserves a conservative maximum before it starts. Text reservations overcount input using UTF-8 bytes, include maximum output, use the highest published context-tier rates, and constrain OpenRouter provider pricing. Image reservations use verified endpoint billing units, pinned providers and a bounded output resolution. A call that will not fit is refused; no subsequent paid request starts over the cap. Actual provider `usage.cost` replaces the reservation when supplied; missing costs and uncertain failures remain **estimates/reservations**, never exact charges. A provider reporting a cost above its advertised reservation stops the job immediately.

Model IDs and fallbacks are explicit configuration in `studio_models.json`. Run `main.py models` to inspect live availability and pricing. Unknown/unavailable, unexpectedly expensive or incompatible text models are excluded; stale catalog data is not used to authorize paid calls.

## What completion means

The pipeline requires validated specs and existing files, C# compilation, explicit assembly evidence, two EditMode tests and ten PlayMode tests. A zero Unity exit code or an empty/skipped NUnit report cannot pass. Runtime tests exercise spawning, horizontal motion, jumping, double-jump prevention, landing/ground detection, coin collision and scoring, assigned references, sprite presence, touch-button movement, menu-to-level and exit-to-results transitions, collision rules and preview rendering. Runtime exceptions fail tests.

The initial run plus at most three repair rounds is allowed. Actual scene errors route to Unity Developer; generated-code and gameplay errors route to QA/coding. Every repair re-runs compilation, assembly and affected tests. Infrastructure/template failures stop explicitly rather than spending on an unrelated generated script. Failed jobs retain diagnostics and never deliver an unverified result as completed.

Requirements that have runtime probes are reported passed only when Unity supplies fresh matching evidence. Visual requirements require manual review. The planner and QA agent record unanswered requirements, unavailable assets, unsupported expectations and other limitations; those keep the result partial. The image service can make sprites and textures, but cannot make 3D models, audio, animation or complete prefab packages. Such requests need licensed files in the optional local `asset_library/` folder or remain explicitly unavailable. The builder can make described procedural geometry and materials; it does not provide a universal asset marketplace. The automatic screenshot is evidence of the rendered scene, not an artistic-quality verdict. No device install or publication happens automatically.

The included platformer, 3D racing and puzzle fixtures in `tests/game_examples.py` are deterministic contract examples. They do not represent paid live-model outputs. To validate an actual generated request, approve it and run its Unity compilation, scene assembly and plan-linked PlayMode probes; each job records the exact results. Unity runs need available disk space (a new isolated copy currently requires at least 1.5 GB).

## Art service status

The real OpenRouter Images API and its model/endpoints catalogs are integrated. Single sprites are requested separately; generated images are not assumed to be sprite sheets. PNG size, dimensions and alpha are checked, imports have explicit PPU/sprite mode, and cache keys include the art direction and asset contract. The asset manifest records model, prompt or original procedural provenance. No external asset library is downloaded.

During verification on this account, image generation returned **HTTP 402: account never purchased credits**. The demonstration therefore uses original procedural PNGs. Adding credits to the correct OpenRouter account enables the implemented paid image path; successful AI image output has not been verified with these credentials. Authentication/payment failures stop further image attempts for that job. This limitation does not prevent building and testing the fallback prototype.
