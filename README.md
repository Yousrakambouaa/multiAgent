# Telegram Unity Studio

A persistent, budgeted supervisor turns an approved `/create <description>` plan into an isolated Unity prototype. Planning uses a versioned specification for any Unity game genre, 2D/3D/mixed scenes, explicitly requested components, multiple scripts and runtime probes. Missing optional details use sensible defaults; they do not block planning. Version 1 saved platformer plans remain readable and keep their original build path. The original Unity project in `generated_game` supplies settings and packages for new projects; keep it alongside the application.

## Launch

From `C:\Users\new\Desktop\studio`:

```powershell
.\.venv\Scripts\python.exe telegram_bot.py
```

Keep one controller running. An OS lock prevents two new bot/CLI controllers from running together; Telegram polling conflicts also stop the duplicate. Stop an older bot before launching the new version.

The existing `.env` supplies `TELEGRAM_BOT_TOKEN`, numeric `TELEGRAM_ALLOWED_USER_ID`, and the selected provider's API key. OpenRouter is the default. See `.env.example` for optional configuration. No existing credential is replaced. A fresh environment can install `requirements.txt`.

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
# Same supervisor, including planning, with an adjustable per-game cap
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
.\.venv\Scripts\python.exe -m tools.run_tests

# Build Android from an unchanged project that has already passed verification
.\.venv\Scripts\python.exe -m tools.build_android
```

CLI `--android` enables the Android development build. For Telegram, set `STUDIO_BUILD_ANDROID=1` before starting the bot. Unity's Android SDK, NDK and JDK must be installed. The APK is for development; signing for publication and installation/device testing are separate, unverified steps.

## Unity MCP

With the default `STUDIO_UNITY_MODE=auto` and MCP enabled, approving a job opens that exact project in a visible interactive Unity Editor before implementation starts. If it is already open, Studio reuses it. The five existing agents and the live QA pipeline operate through that Editor's MCP bridge, and the assembled scenes remain open for inspection. Studio does not launch another Editor for the same project and does not silently fall back to batch mode if the bridge fails. `STUDIO_UNITY_STARTUP_TIMEOUT` bounds Editor and bridge startup (600 seconds by default). Set `STUDIO_UNITY_MCP_ENABLED=0` to opt out; in `auto`, closed projects then use the existing batch path. `live` always requires the matching MCP-connected Editor, while `batch` requires the project to be closed. New job projects receive MCP in their package manifest and a project-local startup hook. The bridge uses Studio's Python interpreter, avoiding the default Python 3.15 that caused the reported PyYAML/MSVC failure. Existing open projects need one asset refresh after MCP is installed so Unity imports the startup hook. The current configured Unity version is Unity 6.

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

When MCP is enabled (the default) and the bridge connects, the five existing agents use role-scoped tools through `ModelService`: scene hierarchy for Supervisor/Designer/Developer, asset search for Art Designer, and console errors for QA. Developer can modify GameObjects/components and save scenes; QA can inspect and validate. Designer/Developer/QA observations remain data in their existing metered model requests, including CrewAI and AutoGen calls. Successful calls record `mcp_tool_used`; bridge failures record `mcp_fallback` and stop live stages with an infrastructure error. MCP starts only in an interactive Editor for that project; the explicit batch path requires the project to be closed.

`mcp-smoke` creates a disposable scene and cube, adds a Rigidbody, creates a temporary material, checks the scene hierarchy/GameObject and reads console errors, then restores the original scene and removes its temporary assets. It refuses to run if the original scene is dirty, multiple scenes are loaded, or the probe paths/names already exist.

Both modes use the existing deterministic scene assemblers and checked evidence. Live mode invokes only the two allowlisted Studio assembly menu entries through the Developer facade; arbitrary menus remain blocked. Each tool call rechecks the connected project. Live compilation imports all assets, requests compilation and requires fresh, completed `Artifacts/compilation.json` evidence from trusted Editor callbacks; historical console errors cannot pass or fail a new compile. Live tests use QA's `run_tests`/`get_test_job`, retain the actual per-test results and apply the existing NUnit and runtime-probe checks. Live failures never fall back to a conflicting batch process. `mcp-smoke` remains the explicit temporary scene-creation check. Setup is injected into generated job projects only; the base `generated_game` project is unchanged.

Version 2 projects persist their selected dimension from planning through project bootstrap, scene validation, assembly evidence, testing and Android builds. Before creating scenes, the Editor preflight resolves every component and property against Unity's actual types, checks typed references and transform/hierarchy data, and rejects invalid collider geometry or mixed physics. QA review findings now enter the bounded repair budget; repaired code/scenes are recompiled, reassembled and rerun through EditMode and PlayMode rather than allowing findings to be ignored.

During development the model first returns scripts, explicit scene data and behavioral probes. Unity scene objects appear after that data passes validation and the C# compiles; the Editor is not modified token by token while the model drafts JSON. Long art/development calls report progress every 30 seconds. A validated implementation is saved before the next QA model call so an interrupted review can resume it.

Standalone version 2 games support up to 32 scripts. Implementation format errors receive up to two corrections, prefer another eligible model after a contract failure, and retain numbered responses and diagnostics across retries. Reports count these corrections separately from Unity repairs. Parsing JSON is recorded as API availability, not as proof that the implementation contract passed. Among models with equal measured results, configured task preferences take priority over unused extra context capacity; pricing, request fit and the job cap still apply.

If a provider explicitly reports an output-token cutoff, the gateway can retry once with up to twice the output allowance (maximum 32,000 tokens), after checking context/output support and reserving the new request within the same cap. This applies to OpenRouter and Dahl.

Small implementation defects can use up to 30 JSON data edits instead of regenerating the entire game. Existing behavioral probes remain unchanged; missing coverage may append new probes. Every edited implementation passes full validation again. OpenRouter requests have a total deadline including provider keepalives (`OPENROUTER_REQUEST_TIMEOUT_SECONDS`, 180 seconds by default, also bounded by `STUDIO_API_TIMEOUT_SECONDS`). Dahl retains its existing timeout. A timeout retains its reservation and switches to another eligible model.

The example configuration prefers task specialists while discovering affordable alternatives from the current account catalog. The preferences include Astra for development/scene repair, Sonnet for design/QA, and image endpoints that support transparent sprites. Batch-only models are rejected on the chat endpoint. Catalog access, capabilities, request size and effective allowance are checked before a call. No model name alone proves game quality; real Unity checks determine the result.

Unity compilation, assembly, EditMode and PlayMode now run before the model's implementation review. Review receives actual test evidence and knows APK creation and Telegram delivery are later stages. It cannot demand those future artifacts before allowing scenes to be assembled. Numeric Unity layers and repeated display names are supported; unique IDs continue to identify objects and test targets.

Art selection filters for transparency before limiting candidates. A rejected image request format is skipped for later matching assets in the same run, and Telegram receives the ready/unavailable asset list with provider reasons. Image failures use procedural PNG placeholders only when the user's request explicitly permits placeholders; their provenance and original failure remain recorded. This fallback covers sprites/textures, not models, audio or animations. Buying more API credit does not correct schema or scene-assembly errors.

Generated C# compiler diagnostics are parsed and deduplicated before failure routing. Recoverable LicensingClient warnings cannot override generated-code errors; `unity_licensing.json` separately records entitlement recovery or required Hub sign-in/license activation. QA reads the current generated files from disk and returns complete replacement files using its configured backend and metered gateway. Repairs preserve scenes, probes and requirements, reject unchanged/advice-only output, persist actual source changes and recompile, with at most three compiler repairs. See `compiler_repairs.json` and job events for evidence. Retrying a saved implementation resumes its locally repaired scripts and art instead of regenerating them; approved changes to the request invalidate that reuse.

## Architecture

| Module | Responsibility |
| --- | --- |
| `telegram_bot.py` | Authorization, commands, background operations, progress and artifact delivery |
| `studio/supervisor.py` | Task graph, plan approval, persistent state, specialist handoffs, bounded repair loop |
| `studio/coordination.py` | Shared game context, dependency gates, evidence-linked task completion and repair invalidation |
| `studio/schemas.py`, `studio/flexible.py` | Version 1 platformer compatibility and version 2 genre-neutral GameSpec, assets, tasks, scene/component contracts, probes, trace and evidence |
| `studio/registry.py`, `studio/model_selection.py`, `studio_models.json` | Provider-scoped catalog cache, capability filtering, observed task evidence, model preferences and paid-model policy |
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

UI Designer and Scene Architect are no longer separate agents. The existing `ui` and `scene` keys in `studio_models.json` remain model task profiles owned by Game Designer and Unity Developer respectively; their OpenRouter model IDs, fallbacks and request limits are unchanged. `AGENT_MODEL_PROFILES` documents this ownership. Compilation, scene building, testing, packaging and APK building remain deterministic tools. Both AI providers use the existing `service.json`/`service.post` boundary.

Default planning uses CrewAI with a custom metered LLM. Default QA repair uses AutoGen with a custom metered model client. They cannot access API credentials or bypass the shared ledger. `STUDIO_DESIGNER_BACKEND=direct` and `STUDIO_QA_BACKEND=direct` select the simpler structured handoff implementation. There are no free-form specialist conversations or unrestricted shell tools.

New version 2 jobs generate multiple named C# components and explicit scene descriptions. The builder does not inject a player, camera, floor, coins, menu or particular physics system. A scene must specify its camera, lighting for 3D, gameplay objects, components and references. Tests load each named scene, execute key/invoke/click steps, check runtime state and save machine-readable evidence linked to requirement IDs. Existing version 1 platformer jobs continue using their maintained runtime and deterministic scene builder. Generated C# is scope-checked and still must compile and pass Unity tests; this is not a security sandbox for malicious code.

Each job lives in `jobs/<id>/`, including a self-contained `UnityProject`, specifications, append-only event log, cost ledger, generated files, Unity logs and validation evidence. `jobs/current.txt` records the current job. Plans wait for `/approve`; a job interrupted after approval resumes on bot startup. Its ledger remains charged and generated images can reuse cache. In-flight calls with unknown billing retain their reservations. A global Unity-job lock prevents overlapping builds, in addition to isolated projects.

## Budget and model policy

`MAX_JOB_BUDGET_USD=10` sets a single configurable maximum. The existing `STUDIO_JOB_BUDGET_USD` default and `STUDIO_MAX_JOB_BUDGET_USD` maximum remain supported when that override is absent. `/budget` and CLI `--budget` still work within this maximum and cannot erase existing charges or reservations. For OpenRouter, spendable funds are the minimum of the remaining job cap, reported key allowance, and accessible account credits, after pending Studio reservations. `/key` accounts for current/reset-period usage and effective budgets; `/credits` is optional because ordinary inference keys may not have access. Missing monetary information is explicitly **unknown**, never assumed to be prepaid credit. Allowance metadata is refreshed during generation; shared, locked reservations coordinate jobs under the same jobs directory. Planning/art preserve 60% for implementation and verification; coding preserves 20% for QA/repairs. Local Unity tests and APK builds need no model charge.

Every attempt reserves a conservative maximum before sending it. Text reservations overcount input using UTF-8 bytes, include maximum output, use the highest published context-tier rates, and constrain OpenRouter provider pricing. Output limits adapt to affordable tokens and model context while retaining a minimum useful response size. Images retain verified endpoint billing units, pinned providers, transparency checks, caching, and bounded output resolution. Actual provider `usage.cost` replaces a reservation when supplied. Explicit OpenRouter pre-inference rejections release unused reservations; timeouts, uncertain failures and missing successful-call costs remain **estimates/reservations**. A provider cost above its reservation halts paid calls. Shared reservations cannot account for other applications using the key or provider reporting delays; actual credit errors therefore remain authoritative.

OpenRouter always discovers account-visible alternatives; `studio_models.json` contains task preferences rather than a whitelist. Legacy configured/paid-only flags do not block affordable catalog or free fallbacks. `STUDIO_FREE_ONLY=1` remains an optional explicit restriction; leave it at 0 for automatic key adaptation. Selection ranks actual Unity task outcomes, structured-output reliability, provider availability, task preferences, declared reasoning support, usable context, and cost. Unmeasured models have neutral evidence rather than invented benchmark results. Each request records its model, capabilities and prices in job events. Catalogs and measured outcomes are scoped to the provider URL and key fingerprint.

If a model request is unavailable, returns unusable structured output, or is denied model-specific access, the same agent request can continue to another eligible model discovered for the selected provider. Dynamic selection ranks the full account-visible catalog, then uses a bounded shortlist of up to three paid text-only models followed by an eligible free fallback when paid use is enabled. It excludes mixed audio/image output models, filters candidates against the current request's context, output-token and remaining-budget limits, and ranks paid choices using recorded validation outcomes before context/capability/cost. Transient retries and model fallbacks remain bounded and each paid attempt gets its own reservation under the existing job cap. OpenRouter credit errors can restrict later attempts to eligible free models; rejected credentials stop immediately because changing models cannot fix a key problem. Catalog/discovery failures are reported before Unity starts. Failure notifications include the attempted models and provider diagnostics (with credentials redacted); the complete attempt record is saved in `job.json` and `events.jsonl`. Studio does not silently switch to a different provider or API key.

Version 2 validation reports summarize job-scoped API, contract and Unity outcomes separately. A paid-model recommendation is emitted only after at least three measured Unity outcomes for both a free baseline and a paid candidate show a meaningful pass-rate advantage; API failures alone are not treated as evidence of weak model quality.

OpenRouter discovery validates the inference key, reads `/models/user`, and discovers image endpoints from `/images/models` with verified billing units. Catalogs expire after five minutes; cache hits still check credentials and allowance. Selection uses at most four candidates per request, retaining a free fallback where available. Explicit affordability hints permit one smaller-token retry. Credit/key-budget rejection switches to eligible free models or preserves the job with the actual blocker. Model restrictions and exhausted rate-limit retries are remembered across later agent calls for the saved job/key. Unsupported JSON modes retry once without the optional mode while still requiring valid JSON. A different key invalidates these restrictions and discovers its own eligible catalog. Authentication failures stop immediately.

Set `AI_PROVIDER=dahl` to use `DAHL_API_KEY` and `DAHL_BASE_URL` (default `https://inference.dahl.global/v1`). `DAHL_MODEL_SUPERVISOR`, `DAHL_MODEL_DESIGNER`, `DAHL_MODEL_DEVELOPER`, `DAHL_MODEL_ART`, and `DAHL_MODEL_QA` hold optional preferences from Dahl's live `/models` catalog; comma-separated IDs specify ordered preferences in configured mode. UI inherits Designer's preferences and scene repair inherits Developer's, while their performance evidence is task-specific. Discovery uses a separate cache and never calls OpenRouter. Dahl has no image endpoints: the planner receives that limitation, and Art keeps local assets, explicit unavailable results for v2 images, and procedural fallback for legacy jobs. Dahl reservations use its [standard $0.02/M token rate](https://inference.dahl.global/pricing); when `usage.cost` is absent, charges remain labeled estimates. A truncated Dahl answer can retry once with a larger output reservation, within the job cap. Restart after changing provider settings.

After editing `OPENROUTER_API_KEY` in `.env`, restart the bot. A variable already set in the launching environment takes precedence over `.env`. OpenRouter keys/models are used only with `AI_PROVIDER=openrouter`; Dahl retains its separate integration. A `401 / User not found` is a rejected credential and cannot be fixed by changing models or increasing a local cap. A displayed key limit is not account credit. Free fallbacks can also have rate limits. No generation can bypass the configured cap.

To verify existing assets without inference: `python -m tools.verify_live --saved-job JOB_ID --android`. This reassembles the saved project, runs real Unity EditMode/PlayMode checks, and builds its APK. It forbids model requests, checks asset hashes/costs stayed unchanged, writes `.studio_cache/saved_asset_verification.json`, and preserves the Telegram current-job marker. Stop the bot first to release its controller lock.

Use a **regular inference API key**, created at OpenRouter's [API Keys page](https://openrouter.ai/settings/keys). Management/provisioning keys can successfully query `/key` and model catalogs but cannot call completion endpoints, even on paid accounts. Preflight checks both `is_management_key` and the legacy `is_provisioning_key` flag and stops with an explanation before model discovery or generation. An account's free/paid tier does not determine this key type. DeepSeek in the default planning profile is selected by configuration, not by assuming the account is free; `:free` suffixes identify the explicit free fallbacks.

Generated implementation contracts are validated before Unity execution. Exact scene display names in probes are resolved to stable IDs; unresolved references report the probe, scene, location and available IDs. Invalid initial contracts receive at most two metered correction attempts. Raw responses and diagnostics are retained as `implementation_response_N.json` and `implementation_diagnostics_N.json` inside the job directory, so failures can be inspected without another API call. Assertions and requirement coverage must remain intact during correction.

## What completion means

Every pipeline requires validated specs and existing files, C# compilation, explicit assembly evidence and real NUnit results. Version 2 runs two scene/reference EditMode tests and a PlayMode harness that executes every requirement-linked probe from a fresh scene. Missing runtime requirement coverage is rejected before Unity starts. The legacy platformer path retains its ten mechanic-specific PlayMode tests. A zero Unity exit code or an empty/skipped NUnit report cannot pass. Runtime exceptions fail checks. Screenshots are retained for human review; arbitrary games are not required to contain a fixed button color or touch UI.

The initial run plus at most three repair rounds is allowed. Actual scene errors route to Unity Developer; generated-code and gameplay errors route to QA/coding. Every repair re-runs compilation, assembly and affected tests. Infrastructure/template failures stop explicitly rather than spending on an unrelated generated script. Failed jobs retain diagnostics and never deliver an unverified result as completed.

## Stabilization and audit commands

Telegram's catch-all previously returned the complete `/start` menu for every unmatched update. Python Telegram Bot's command handler only recognizes a `BOT_COMMAND` entity at offset zero addressed to this bot; pasted/indented commands, caption commands and unknown commands could therefore reach the menu response. Normal `/create` registration was already correct. The fallback now recovers known commands, preserves their descriptions, ignores commands addressed to another bot, and gives a short usage response for unsupported messages. Long plans are split into Telegram-safe messages, so an oversized plan cannot silently lose its approval instructions. Native command-entity dispatch and fallback recovery have separate regression tests. The exact payload of the earlier reported incident was not recorded, so its entity/addressing failure cannot be proven retrospectively.

An explicit `/create` supersedes an idle waiting plan; a new game is never appended as an instruction to a running build. Ordinary follow-up details retain their existing behavior. Operation tracking uses the actual task, duplicate update IDs are bounded and deduplicated, cancelled planning releases the job, and an interrupted planning job with no saved specification cannot auto-resume as a build. Restart the bot after source or `.env` changes: a running Python process keeps its loaded code.

The five existing agents remain. UI is a Designer task using the `ui` profile; scene repairs are Developer tasks using `scene`. `shared_context.json` records the original/effective prompt, specification hash, requirement IDs, installed project version and ownership. Handoffs enforce stage dependencies, and `work_items.json` only marks requirement-linked work verified after its dependencies and checks pass. Repairs invalidate prior stage evidence, reject unchanged responses and stop on repeated failures. Component checks reject mixed physics, missing required bodies and legacy input calls before Unity; the assembler wires objects before activating them and supplies Unity 6's built-in UI font when needed. Visual reviews remain manual.

The former generic PlayMode harness required a specific blue touch-button palette and white UI text for every genre. Those assertions were removed; game-specific controls/state probes and runtime errors remain authoritative, and screenshots are saved as visual-review evidence.

Stop the Telegram controller before running these commands. Real audits use `jobs/verification_runs/`, retain reports, obey the configured cap, and leave `jobs/current.txt` intact:

```powershell
Set-Location C:\Users\new\Desktop\studio
.\.venv\Scripts\python.exe -m tools.run_tests
.\.venv\Scripts\python.exe -m tools.verify_live --preflight
# Real provider planning, implementation and Unity verification; consumes inference tokens.
.\.venv\Scripts\python.exe -m tools.verify_live
# Deterministic engine audits, explicitly NOT AI-generated games; no API calls.
.\.venv\Scripts\python.exe -m tools.verify_live --unity-fixture racing
.\.venv\Scripts\python.exe -m tools.verify_live --unity-fixture puzzle
# Retry a saved fixture after its first package import, instead of creating another project.
.\.venv\Scripts\python.exe -m tools.verify_live --unity-fixture racing --resume-fixture <job-id>
.\.venv\Scripts\python.exe telegram_bot.py
```

Pattern references studied: [UniGen](https://github.com/yxwan123/UniGen) for structured Unity blueprints and diagnostic-driven repair; [game-mcp](https://github.com/xthenyo/game-mcp) for task ownership and QA gates; [ChatDev](https://github.com/OpenBMB/ChatDev) for explicit workflow/context flow; [ai-game-studio](https://github.com/fl0wsam/ai-game-studio) for a shared blackboard and layered validators. These patterns were adapted to the existing contracts and tools, without importing their frameworks or assuming their advertised reliability applies here.

Requirements that have runtime probes are reported passed only when Unity supplies fresh matching evidence. Visual requirements require manual review. The planner and QA agent record unanswered requirements, unavailable assets, unsupported expectations and other limitations; those keep the result partial. The image service can make sprites and textures, but cannot make 3D models, audio, animation or complete prefab packages. Such requests need licensed files in the optional local `asset_library/` folder or remain explicitly unavailable. The builder can make described procedural geometry and materials; it does not provide a universal asset marketplace. The automatic screenshot is evidence of the rendered scene, not an artistic-quality verdict. No device install or publication happens automatically.

The included platformer, 3D racing and puzzle fixtures in `tests/game_examples.py` are deterministic contract examples. They do not represent paid live-model outputs. To validate an actual generated request, approve it and run its Unity compilation, scene assembly and plan-linked PlayMode probes; each job records the exact results. Unity runs need available disk space (a new isolated copy currently requires at least 1.5 GB).

## Art service status

The real OpenRouter Images API and its model/endpoints catalogs are integrated. Single sprites are requested separately; generated images are not assumed to be sprite sheets. PNG size, dimensions and alpha are checked, imports have explicit PPU/sprite mode, and cache keys include the art direction and asset contract. The asset manifest records model, prompt or original procedural provenance. No external asset library is downloaded.

During verification on this account, image generation returned **HTTP 402: account never purchased credits**. The demonstration therefore uses original procedural PNGs. Adding credits to the correct OpenRouter account enables the implemented paid image path; successful AI image output has not been verified with these credentials. Authentication/payment failures stop further image attempts for that job. This limitation does not prevent building and testing the fallback prototype.
