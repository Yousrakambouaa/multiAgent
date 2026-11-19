# Telegram Unity Studio

A persistent, budgeted supervisor turns an approved platformer idea into an isolated Unity project, assembles scenes, and checks the result in the real Unity Editor. The original Unity project in `generated_game` supplies settings and packages for new projects; keep it alongside the application.

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

1. `/create A woodland platformer with jumping, coins and patrolling enemies`
2. `/plan` to inspect the game plan and `/budget` to inspect charges and reservations.
3. `/approve` to generate and verify the project.
4. `/status` during the build; `/cancel` to request cancellation.
5. `/result` to retrieve the source archive, validation report, instructions, captured preview and NUnit results. Verified artifacts are also sent automatically after a successful build. An APK is included only after a successful Android build and archive integrity check.

`/start` introduces the bot; `/myid` reports the sender's own Telegram ID. Every command that reads or changes jobs checks the configured authorized user ID. Plain game requests use the same workflow. `tt` is rejected without an API call. Unrelated requests are rejected, and vague requests ask for a specific game. Other genres are explicitly unsupported by this MVP, rather than silently becoming platformers.

## CLI

```powershell
# Same supervisor, including real planning, under the default $2 cap
.\.venv\Scripts\python.exe main.py create "Create a woodland platformer with coins"
.\.venv\Scripts\python.exe main.py plan
.\.venv\Scripts\python.exe main.py approve

# Fully authorized unattended demonstration
.\.venv\Scripts\python.exe main.py create "Create a woodland platformer with coins" --approve

# Deterministic example plan, with live coding/UI/art stages after approval
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

## Architecture

| Module | Responsibility |
| --- | --- |
| `telegram_bot.py` | Authorization, commands, background operations, progress and artifact delivery |
| `studio/supervisor.py` | Task graph, plan approval, persistent state, specialist handoffs, bounded repair loop |
| `studio/schemas.py` | Strict GameSpec, SceneBundle, SceneSpec, UIPlan and C# bundle validation |
| `studio/registry.py`, `studio_models.json` | Live model availability/capability/pricing lookup; explicit primary/fallback allowlists |
| `studio/api.py`, `studio/costs.py` | Shared OpenRouter gateway, pre-call reservations, reported charges, conservative unknown-charge estimates |
| `studio/agents.py`, `studio/frameworks.py` | Designer, developer, UI, scene and QA specialists; metered CrewAI and AutoGen adapters |
| `studio/assets.py` | Real Image API calls, PNG validation/resizing, alpha checks, cache, provenance and original procedural fallback |
| `studio/scenes.py` | Deterministic Scene Architect compilation from the shared game/UI/art specifications |
| `studio/templates/Runtime` | Complete reusable platformer physics, input, scoring, enemies, camera, menus and touch controls |
| `studio/templates/Editor` | General scene/component/property/reference assembly, sprite import, prefabs, build settings and Android build |
| `studio/templates/Tests` | Real Unity EditMode and PlayMode suites |
| `studio/unity.py` | Fixed Unity CLI invocations, cancellation/timeouts, fresh evidence and NUnit parsing |
| `studio/artifacts.py` | Source packaging, reports, artifact hashes and verified-APK filtering |
| `studio/verification.py` | Local re-verification without additional model calls |

Default planning uses CrewAI with a custom metered LLM. Default QA repair uses AutoGen with a custom metered model client. They cannot access API credentials or bypass the shared ledger. `STUDIO_DESIGNER_BACKEND=direct` and `STUDIO_QA_BACKEND=direct` select the simpler structured handoff implementation. There are no free-form specialist conversations or unrestricted shell tools.

The developer generates `GeneratedRules.cs` against a fixed, complete runtime contract. Core movement and UI use maintained templates so a small model does not repeatedly invent physics or dependencies. The scene assembler resolves component types and actual serialized/public property metadata; it does not assume every game has scripts called `PlayerMovement` or `CoinController`. New genres need new verified runtime adapters and tests.

Each job lives in `jobs/<id>/`, including a self-contained `UnityProject`, specifications, append-only event log, cost ledger, generated files, Unity logs and validation evidence. `jobs/current.txt` records the current job. After an interrupted process, an existing plan can be retried with `/approve`; its ledger remains charged and generated images can reuse cache. In-flight calls with unknown billing retain their reservations. A global Unity-job lock prevents overlapping builds, in addition to isolated projects.

## Budget and model policy

`STUDIO_JOB_BUDGET_USD` defaults to 2. Every paid attempt reserves a conservative maximum before it starts. Text reservations overcount input using UTF-8 bytes, include maximum output, use the highest published context-tier rates, and constrain OpenRouter provider pricing. Image reservations use verified endpoint billing units, pinned providers and a bounded output resolution. A call that will not fit is refused; no subsequent paid request starts over the cap. Actual provider `usage.cost` replaces the reservation when supplied; missing costs and uncertain failures remain **estimates/reservations**, never exact charges. A provider reporting a cost above its advertised reservation stops the job immediately.

Model IDs and fallbacks are explicit configuration in `studio_models.json`. Run `main.py models` to inspect live availability and pricing. Unknown/unavailable, unexpectedly expensive or incompatible text models are excluded; stale catalog data is not used to authorize paid calls.

## What completion means

The pipeline requires validated specs and existing files, C# compilation, explicit assembly evidence, two EditMode tests and ten PlayMode tests. A zero Unity exit code or an empty/skipped NUnit report cannot pass. Runtime tests exercise spawning, horizontal motion, jumping, double-jump prevention, landing/ground detection, coin collision and scoring, assigned references, sprite presence, touch-button movement, menu-to-level and exit-to-results transitions, collision rules and preview rendering. Runtime exceptions fail tests.

The initial run plus at most three repair rounds is allowed. Actual scene errors route to Scene Architect; generated-code and gameplay errors route to QA/coding. Every repair re-runs compilation, assembly and affected tests. Infrastructure/template failures stop explicitly rather than spending on an unrelated generated script. Failed jobs retain diagnostics and never deliver an unverified result as completed.

Not all gameplay is tested: enemy stomping, full level traversal, physical touchscreen behavior, multiple-level progression, accessibility and aesthetic quality still need review. The automatic screenshot is evidence of the rendered scene, not an artistic-quality verdict. No device install or publication happens automatically.

## Art service status

The real OpenRouter Images API and its model/endpoints catalogs are integrated. Single sprites are requested separately; generated images are not assumed to be sprite sheets. PNG size, dimensions and alpha are checked, imports have explicit PPU/sprite mode, and cache keys include the art direction and asset contract. The asset manifest records model, prompt or original procedural provenance. No external asset library is downloaded.

During verification on this account, image generation returned **HTTP 402: account never purchased credits**. The demonstration therefore uses original procedural PNGs. Adding credits to the correct OpenRouter account enables the implemented paid image path; successful AI image output has not been verified with these credentials. Authentication/payment failures stop further image attempts for that job. This limitation does not prevent building and testing the fallback prototype.
