# AI Unity Game Studio

AI Unity Game Studio is a local Python application that turns a game description into a Unity prototype. You can use it through a Telegram bot or the command line. It plans the game, requests scripts and scene data from language models, prepares artwork, asks Unity to assemble and test the project, and can attempt an Android APK build.

The original goal was ambitious: let a small team of AI agents make complete, high-quality games while choosing affordable models and keeping total API spending low. The implementation is an experiment toward that goal. It is useful for studying orchestration, generated code, validation, and the real cost of repairs. It does not reliably turn every prompt into a finished game, and it does not establish that agent-driven development is cheaper than ordinary development.

## What works, and where the limits are

The current workflow supports structured specifications for 2D, 3D, and mixed Unity projects. It preserves an explicitly requested dimension, generates multiple C# scripts, constructs scenes from validated data, imports supported artwork, and records requirement-linked test results. Older saved 2D platformer jobs still have a separate compatibility path.

A successful run can produce a Unity source archive, validation reports, a preview, test results, and an APK. A run may instead be **partial**, **failed**, **budget-stopped**, or **interrupted**. Those states matter: compiling C# is not proof of good gameplay, and an APK file is not proof that the game was installed or played on a device.

Important limitations:

- Generated implementations can contain invalid references, incorrect physics, missing behavior, or incomplete UI. Corrections and repairs are bounded; they do not continue indefinitely.
- The newer contract accepts up to 32 scripts, 30 scenes, and 40 requested assets. These are validation ceilings, not claims that large projects are practical within a small budget or context window.
- Image generation covers sprites and textures through supported image endpoints. The studio does not contain a general AI generator for 3D models, music, or audio. Native assets can come from a licensed local library; otherwise unavailable assets are reported.
- In the newer workflow, requested artwork is not silently replaced with placeholders unless the request permits that. The legacy platformer path can use its documented procedural fallback.
- Automated probes check the behavior represented in the specification. They can miss defects, and model-generated probes are not an independent assessment of a game's design or polish.
- There is no automatic real-device testing, commercial release certification, app-store publishing, multiplayer hosting, or production security sandbox.
- Windows with Python 3.12 and the supplied Unity version is the exercised environment. Other operating systems require path and tool configuration and have not received equivalent end-to-end verification.

## Architecture: five agents, seven task profiles

An **agent** here means a specialist workflow with a responsibility, a model-backed task, and access to a controlled set of tools. There are exactly five registered agent identities. The seven keys in `studio_models.json` are task-specific model profiles, not seven separate agents.

| Agent | Responsibilities | Model profiles |
| --- | --- | --- |
| Supervisor | Owns the job, approvals, handoffs, budgets, cancellation, failure routing, and packaging | `supervisor` |
| Game Designer | Interprets the request, writes requirements and a game specification, and plans UI | `designer`, `ui` |
| Unity Developer | Produces scripts, scene descriptions, gameplay probes, and scene repairs | `developer`, `scene` |
| Art Designer | Generates supported images, imports licensed files, reuses assets, and reports missing artwork | `art` |
| QA & Debugger | Reviews implementation and evidence, repairs generated code, and participates in bounded validation cycles | `qa` |

The initial Developer response already contains scene data. The `scene` profile is used for scene repairs; it does not add another LLM call for every assembled scene. Actual assembly and test execution use deterministic Python/C# tools. The Supervisor is orchestration code, not an LLM deciding every action.

### Generation workflow

1. **Receive a request.** Telegram checks the configured user ID for job operations. `/create` explicitly requests a game; ordinary text can pass through a classifier. The CLI uses the same supervisor.
2. **Create a saved job.** A dated directory under `jobs/` records the request, budget, stages, costs, and cancellation state.
3. **Discover and plan.** The provider registry checks credentials and model access. Designer produces a typed specification with requirements, assumptions, scenes, asset workflows, and task dependencies.
4. **Approve the plan.** Planning itself can spend API credit. `/approve`, or the CLI's `--approve` flag, authorizes implementation. The app does not bypass this gate simply because it has a valid key.
5. **Prepare Unity.** The selected seed settings and packages are copied into the job project. Runtime code, Editor builders, tests, and MCP startup code come from `studio/templates/`. The chosen game dimension is recorded before implementation.
6. **Prepare UI and art.** UI is planned, existing assets are reused, supported images are requested, and licensed library files are imported. Ready and unavailable assets are listed in `art_manifest.json`.
7. **Generate and validate implementation data.** Scripts, scene objects, component properties, references, and behavioral probes are checked before Unity assembly. Numbered model responses and diagnostics are retained when contracts fail.
8. **Compile, assemble, and test.** Unity imports the files and compiles C#. A trusted Editor builder creates and saves actual scene objects. EditMode checks inspect structure; PlayMode probes exercise the declared behavior. QA reviews the resulting evidence.
9. **Repair within limits.** Concrete failures are routed to the existing specialists. Repairs preserve existing probes and requirements, reject advice-only or unchanged output, and rerun affected stages. Repeated failures, unavailable infrastructure, cancellation, or budget limits stop the run.
10. **Package and deliver.** If requested and supported by the installed toolchain, Unity attempts Android packaging. The studio records evidence and artifact hashes, creates an archive, and makes verified or explicitly partial results available through `/result`.

On restart, an interrupted build can resume from saved inputs and implementation. Completed local scripts and artwork are reused when valid. Planning interrupted before a usable specification is saved is marked failed instead of pretending it can resume a build. Keep the entire job directory if you need recovery.

### Technologies in the implementation

| Technology | Its role |
| --- | --- |
| Python and `asyncio` | Orchestration, asynchronous requests, job coordination, and CLI/bot entry points |
| Unity 6000.3.24f1 and C# | Game runtime, Editor scene builders, physics, compilation, tests, and Android builds |
| Input System, uGUI, and URP | Input and UI support; the seed contains 2D URP assets, which are filtered for 3D/mixed projects |
| `python-telegram-bot` | Telegram polling, commands, authorization, and artifact delivery |
| OpenRouter and HTTPX | Provider/model discovery, text and supported image requests, prices, and allowance metadata |
| Dahl | An alternative text-only inference provider with separate credentials, catalog, cache, and routing |
| Pydantic | Typed contracts and validation of specifications, scripts, scenes, edits, and probes |
| Pillow | PNG decoding, resizing, transparency checks, cached artwork, and procedural images |
| CrewAI | Optional Designer wrapper, enabled by default; delegates inference to the same metered gateway |
| AutoGen AgentChat | Optional QA wrapper, enabled by default; also uses the metered gateway |
| Unity MCP, Coplay, and `uvx` | The local bridge between trusted orchestration and the matching Unity Editor |
| `unittest`, Unity Test Framework, and NUnit | Mocked Python regressions and actual Unity Editor/runtime checks |
| JSON, `decimal`, and `zipfile` | Saved state and contracts, cost arithmetic, source archives, and package checks |
| `python-dotenv` and PowerShell | Local configuration loading and a Windows launcher |

CrewAI and AutoGen are adapters inside these five specialist workflows. They do not receive separate provider keys or an unlimited budget. `STUDIO_DESIGNER_BACKEND=direct` and `STUDIO_QA_BACKEND=direct` bypass the optional wrappers while retaining the same gateway and validation pipeline.

## Model selection and API cost

### The catalog is a preference list

`studio_models.json` supplies model preferences for each profile. `Registry.DEFAULTS` in `studio/registry.py` is used if that file is absent. These entries are not a whitelist or proof of access: the account-visible provider catalog must contain an eligible model with usable capabilities and pricing.

The checked-in preferences currently contain these families and IDs:

| Family | IDs in the catalog |
| --- | --- |
| DeepSeek | `deepseek/deepseek-v4.1-flash-1017` |
| MiniMax | `minimax/minimax-m3.1-flash` |
| Qwen | `qwen/qwen3.8-flash`, `qwen/qwen3-coder-next` |
| GLM | `z-ai/glm-5.3-flash` |
| Gemini | `google/gemini-3.8-flash` |
| Claude | `anthropic/claude-sonnet-5.5` |
| Image endpoints | `bytedance-seed/seedream-5-5`, `recraft/recraft-v4.1-flash-v2`, `black-forest-labs/flux.2-klein-4b` |

See the JSON file for the exact ordering by task. Model IDs, access rules, endpoints, and prices can change. An unavailable preference can be skipped in favor of an eligible discovered alternative. There is no maintained price table in this README because the code reads current catalog prices.

### Hybrid selection

New OpenRouter jobs default to `STUDIO_OPENROUTER_POLICY=hybrid`. Their policy is saved with the job. The registry filters for text/JSON suitability, context and output capacity, known availability, price validity, and the request's spendable budget before ranking candidates.

The ranking estimates the cost of the request **and a possible recovery**, rather than always choosing the cheapest token price or the largest premium model. Ordinary requests start in an economical catalog price band. Task complexity, a failed validation, or poor measured validation history can open the broader pool. Preferences receive a small bonus rather than overriding cost and fit. Up to four shortlisted models are considered, with bounded adaptation and fallback.

Local Unity and contract outcomes carry more weight than the fact that an API returned JSON. Optional Artificial Analysis indices from OpenRouter's [benchmark metadata endpoint](https://openrouter.ai/docs/api/api-reference/benchmarks/list-benchmarks) provide an advisory starting signal, matched to an exact model or canonical slug. They are not proof of Unity C# quality, and missing indices remain unknown. Benchmark metadata is cached for a day; unavailable metadata gets a five-minute retry cooldown. Model catalogs have a five-minute cache tied to the provider/key fingerprint, with credential checks on refresh.

No LLM is called merely to choose another LLM. No paid comparative benchmark runs automatically. The expected-cost score is a heuristic, not a demonstrated globally optimal price/quality strategy.

Provider-specific 429s, timeouts, invalid responses, and unsupported optional parameters can trigger bounded adaptation or another eligible model. Known-blocked models have cooldowns. Explicit account-wide rate limits preserve Retry-After state rather than trying every model. Authentication failures stop immediately. Credit/key-budget failures may allow an eligible free fallback, but cannot manufacture credit or remove a provider limit. Uncertain timeout charges stay reserved.

Image selection is separate. Endpoints must meet the existing price, format, and requested-transparency checks. Usable-image history and reference-image support can improve ranking; neither is an aesthetic guarantee. Saved ready assets and matching cached images are reused. No general paid-only restriction is imposed on OpenRouter; `STUDIO_FREE_ONLY=1` explicitly restricts eligible endpoints to zero-priced ones, which can still be unavailable or rate-limited. Legacy routing settings are retained for Dahl.

### Spending and reservations

A local cap is permission to spend, not an account balance. Spendable funds are bounded by the remaining job cap, accessible OpenRouter key/account allowance, and pending Studio reservations. Key limits, reset-period usage, account credits, and concurrent reservations are considered where metadata is available. `/credits` can be unavailable to an inference key; unknown allowance is labeled unknown, and real provider rejections remain authoritative.

Before a request, the ledger persists a conservative reservation. Text quotes overestimate input with UTF-8 bytes plus overhead, include the maximum requested output, use the highest published applicable rates, and include a small margin for paid requests. Published per-token prices are multiplied by the estimated input and allowed output; image quotes use the endpoint's image or megapixel billing units. Reasoning consumes output allowance too, so a response can run out of tokens before producing its JSON.

Provider `usage.cost`, when supplied, replaces the reservation. Missing costs and uncertain failures remain estimates; explicit pre-inference rejections can release unused reservations. A provider cost above the verified reservation halts further paid calls. Planning/UI/art preserve 60% of the effective budget for downstream work; coding/scene requests preserve 20% for QA and repairs. Local assembly, testing, and APK building do not themselves require an LLM charge.

The default settings are a $10 job cap and a $50 configurable upper limit. `/budget AMOUNT` and CLI `--budget AMOUNT` can change the local cap within that limit; they cannot erase existing commitments. `MAX_JOB_BUDGET_USD`, when set, overrides both legacy environment values. These numbers are configurable defaults, not a promise that the account contains that money. Shared reservations coordinate Studio jobs under one jobs directory; they cannot account perfectly for other applications using the key, provider reporting delays, or stale price metadata.

Reports separate provider-reported dollars from estimates and reservations. Hybrid reports include cost by agent/profile, attempted calls, cost per successfully validated task, and completed-game cost when the game is actually marked completed. Failed attempts count toward cost. A model response or a partial game is not silently counted as a successful completed game.

## Requirements and installation

The documented setup is Windows PowerShell with Python **3.12**, Git, and Unity **6000.3.24f1**. Use the version recorded in `generated_game/ProjectSettings/ProjectVersion.txt` rather than assuming a different Editor release is interchangeable.

You need internet access to download Python/Unity/MCP dependencies, access a provider, and use Telegram. A valid provider inference key is required for generation, but not for the mocked Python tests. Unity needs a usable license/Hub sign-in. New game projects require at least 1.5 GB available disk space at the bootstrap check, and imports or Android builds can need substantially more.

### 1. Install Python dependencies

Replace `YOUR_REPOSITORY_URL` with the repository address you intend to use:

```powershell
git clone YOUR_REPOSITORY_URL unity-game-studio
cd unity-game-studio
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

The top-level Python requirements are pinned, including compatibility pins for protobuf and googleapis-common-protos. This is not a complete transitive dependency lock: a later installation can resolve different indirect packages. The release preparation checks use a separate fresh environment; the active developer environment is not modified.

For the default Unity MCP workflow, install the `uv` command-line tools if they are not already present:

```powershell
.\.venv\Scripts\python.exe -m pip install uv
.\.venv\Scripts\uvx.exe --version
```

The sample configuration points directly at this virtual environment's `uvx.exe`, so shell activation is not required. On another operating system, use its Python/venv paths and change the MCP command and Unity Editor path accordingly.

### 2. Configure the local environment

```powershell
Copy-Item .env.example .env
```

Edit **`.env`**, not `.env.example`. The sample explains every supported setting, including the optional framework backends and Dahl role preferences.

| Setting | Meaning |
| --- | --- |
| `AI_PROVIDER` | `openrouter` or `dahl`; the studio does not silently switch providers |
| `OPENROUTER_API_KEY` / `DAHL_API_KEY` | The selected provider's own inference key |
| `TELEGRAM_BOT_TOKEN` | Your bot's token; needed only for Telegram transport |
| `TELEGRAM_ALLOWED_USER_ID` | Numeric ID allowed to operate jobs; `0` is a setup-only value that authorizes nobody |
| `UNITY_EDITOR_PATH` | Path to your installed Unity executable |
| `STUDIO_JOB_BUDGET_USD` | Default requested job cap |
| `STUDIO_MAX_JOB_BUDGET_USD` | Maximum accepted by the budget controls |
| `MAX_JOB_BUDGET_USD` | Optional single override for both legacy budget values |
| `STUDIO_OPENROUTER_POLICY` | `hybrid` for the current request-specific value policy |
| `STUDIO_FREE_ONLY` | Optional restriction to zero-priced OpenRouter endpoints |
| `STUDIO_BUILD_ANDROID` | Requests Android packaging by default when set to `1` |
| `STUDIO_UNITY_MODE` | `auto`, `live`, or `batch` |
| `STUDIO_UNITY_MCP_ENABLED` | Enables the local Editor bridge |

Restart the bot after changing `.env`, model preferences, or provider configuration. Existing nonblank shell environment values take precedence over `.env`; a stale inherited key can therefore hide an edited file. Blank provider/key environment values receive special handling in `studio/config.py`. Do not put tokens into launch commands, screenshots, game prompts, or issue reports.

### 3. Set up Telegram

Create a bot with [Telegram's BotFather](https://core.telegram.org/bots/features#botfather) and put its token in `.env`.

For first-time ID setup, keep `TELEGRAM_ALLOWED_USER_ID=0`, launch the bot, and send `/myid` to your own bot in a private chat. Stop the bot, replace `0` with the returned numeric ID, and restart. `/start`, `/help`, and `/myid` are public introduction/self-ID commands; commands that read or change job data enforce the configured user ID.

Launch from the repository root:

```powershell
.\.venv\Scripts\python.exe telegram_bot.py
```

Or use the Windows launcher:

```powershell
powershell -ExecutionPolicy Bypass -File .\start_bot.ps1
```

The launcher checks for another bot and the held controller lock. It waits rather than deleting locks or stopping Unity. Run only one polling process per Telegram token. The bot and CLI also share a controller lock, so stop the bot before using CLI commands against the same checkout.

### 4. Set up Unity and MCP

Install the specified Editor through Unity Hub and open it once to complete licensing and installation prompts. For APK output, add **Android Build Support**, the Android SDK and NDK tools, and OpenJDK for that Editor. Unity's [Android environment setup](https://docs.unity3d.com/6000.3/Documentation/Manual/android-sdksetup.html) describes those dependencies. The build path uses IL2CPP and ARM64; the output is a development APK, not a signed store release workflow.

With `STUDIO_UNITY_MODE=auto` and MCP enabled, the approved job opens in a visible Editor. New projects receive the Coplay Unity package pinned to `v10.3.0`, a project-local interpreter configuration, and a startup hook. The stdio server is launched through `mcpforunityserver==10.3.0`. Git and the package servers must be reachable during first-time installation.

The app verifies that the bridge belongs to the exact job project before allowing agent tools. It does not intentionally start a conflicting batch Editor for an already-open project or quietly switch projects after a bridge failure. Allow Unity to finish package import and C# compilation before diagnosing the connection. Open a generated `jobs/JOB_ID/UnityProject` when inspecting a game; `generated_game/` is the seed, not the current game.

If you deliberately do not want MCP, set `STUDIO_UNITY_MCP_ENABLED=0`. In `auto` mode, closed projects can then use batch execution. Explicit `batch` mode requires the project to be closed; `live` requires a working bridge. This alternative does not provide the same visible live-editing workflow.

## Generate and inspect a game

A small, specific request is easier to validate than a request for an entire commercial game. State the dimension, controls, target, core mechanics, and what counts as success. For an initial desktop experiment, set `STUDIO_BUILD_ANDROID=0`; leave it enabled when APK output is required.

Example Telegram request:

```text
/create Make a small 3D desktop collection game. Move a cube with WASD on a bounded platform, collect three spheres to win, show the score, and press R to restart. Use colored primitives and one scene.
```

Read the plan with `/plan`, inspect the cap with `/budget`, then send `/approve`. During generation, `/status` shows stages and `/cancel` requests cancellation. `/result` returns the available artifacts after completed or explicitly partial runs. Artifact hashes are checked before delivery; a changed or missing file is not sent as verified. Telegram upload limits can require using the local output instead.

You can add instructions while a job exists. The supervisor records whether they were incorporated during planning, require the next approval, or arrived too late and were queued. A late message does not silently rewrite an already-running Unity build.

Equivalent CLI commands, with the bot stopped:

```powershell
.\.venv\Scripts\python.exe main.py create "Make a small 3D collection game using primitives and WASD." --budget 2
.\.venv\Scripts\python.exe main.py plan
.\.venv\Scripts\python.exe main.py approve --android
.\.venv\Scripts\python.exe main.py status
```

`create` already calls the planning model. `create --approve` combines planning and implementation; `--android` explicitly requests an APK. Global Android defaults still apply, so set `STUDIO_BUILD_ANDROID=0` if an APK is not intended. `main.py demo --approve` uses the legacy platformer path; it is not evidence that arbitrary genres have been generated successfully.

Metadata and bridge diagnostics:

```powershell
.\.venv\Scripts\python.exe main.py models
.\.venv\Scripts\python.exe main.py mcp-status
```

`models` makes credential/catalog/price metadata requests rather than a completion. Its compatible catalog preview is not a promise about which model a particular hybrid task will use. `mcp-status` requires a current generated project and bridge. `mcp-smoke` goes further by making and cleaning up temporary scene/asset changes; use it only when the Editor is idle with a saved, clean scene.

### Saved outputs

A normal local job looks like this; not every file exists after a failed or interrupted stage:

```text
jobs/JOB_ID/
  job.json                 # status, stage, budget, costs, artifacts
  events.jsonl             # handoffs, model choices, failures, tool calls
  game_spec.json
  ui_plan.json
  art_manifest.json
  implementation.json
  scene_spec.json
  requirements.json
  validation_report.json
  unity_prototype.zip
  PLAY.txt
  UnityProject/
    Assets/
    Packages/
    ProjectSettings/
    Artifacts/             # assembly evidence, XML results, preview, APK
```

`implementation_response_*.json` and `implementation_diagnostics_*.json` preserve failed contract attempts. Other reports and logs are added by the stages that need them. Local cache data lives in `.studio_cache/`; optional licensed inputs belong in a user-created `asset_library/` directory. These locations are excluded from Git.

The source ZIP includes job/report metadata and can include local MCP configuration. It is **not a sanitized public export**. Inspect prompts, identifiers, interpreter paths, artwork licenses, and reports before sharing generated archives.

## Testing and verification

### Offline Python regressions

No provider credentials or running Unity Editor are needed:

```powershell
.\.venv\Scripts\python.exe -m tools.run_tests
```

This runs the existing `unittest` suite with mocked provider responses, framework adapters, and Unity/MCP interactions. It covers selection, budgets, 402/403/429 handling, JSON/parameter fallback, saved-stage recovery, five-agent ownership, scene contracts, and Telegram authorization. A passing mocked test does not establish live model quality or a real Unity build.

For focused policy tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_hybrid_selection.py -v
```

### Explicit Unity checks without new inference

With the bot stopped and Unity correctly installed:

```powershell
.\.venv\Scripts\python.exe -m tools.verify_live --unity-fixture racing
.\.venv\Scripts\python.exe -m tools.verify_live --unity-fixture puzzle
.\.venv\Scripts\python.exe -m tools.verify_live --saved-job JOB_ID --android
```

The fixtures exercise deterministic test plans; they are not AI-generated games. The saved-job audit reuses existing assets, forbids model requests, checks their hashes and the API cost ledger, and reassembles/tests the project before an optional APK build. It writes local evidence under `.studio_cache/` and does not replace the Telegram current-job marker. It does mutate/repackage the selected local project and requires its normal Unity locks.

`main.py verify` also rechecks the current saved project without regenerating it. Read its Android default before running it. `tools.build_android` is a narrower helper for an unchanged verified archive and has additional target/evidence checks. Running `tools.verify_live` with no fixture or saved-job option performs a live generation audit and **can spend API credit**; it is not an offline test command.

### What has actually been verified

Earlier local work recovered a saved 2D prototype through real Unity compilation and scene assembly, four EditMode tests, one PlayMode harness containing eleven gameplay checks, and an Android APK build. Saved image references and unchanged image hashes were also checked. That result does not prove the newer hybrid policy can generate a fresh game end to end, and the prototype retained partial/unverified requirements.

The hybrid-policy work had a passing 279-test mocked regression suite. Public-release preparation additionally checks installation in a new Python environment and tests a clean source export without local credentials or generated jobs; the final preparation report records the exact run. Historical private diagnostics, generated games, and APKs are not distributed as reproducible benchmark results.

Fresh paid-model comparisons, autonomous hybrid generation through Telegram delivery, real-device installation/performance, subjective game quality, and equivalent coverage on other operating systems remain unverified. Repeating the same prompt with changing provider/model catalogs is not guaranteed to produce the same game or cost.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| 401 / rejected key | Use the selected provider's valid inference key. OpenRouter management/provisioning keys cannot run models. Check inherited environment values and restart. More local budget does not repair authentication. |
| 402 / insufficient credits | Check actual provider credit and key allowance, not just the local cap. Read the saved attempt and reservation details before retrying. |
| 403 / access or weekly key limit | A model restriction may permit fallback; an account/key limit requires addressing that limit. Increasing the studio cap alone does not reset it. |
| 429 / rate limit | Respect Retry-After. Provider-specific failures and explicit account-wide limits are handled differently; inspect the recorded scope/reason. |
| Sprites exist but the scene is empty | Scene assembly follows valid implementation data and C# compilation. Check `stage`, implementation diagnostics, and Unity compilation/assembly evidence. Asset generation by itself does not create scene objects. |
| Artwork is unavailable | Check the manifest for endpoint, transparency, library, budget, or provider limitations. Images do not substitute for missing 3D-model/audio tools. |
| Broken generated JSON or missing probe references | Inspect the numbered responses and diagnostics. Contracts and behavioral coverage must pass; do not weaken assertions merely to obtain a passing report. |
| Unity is open but MCP fails | Verify the job/project path, package import, interpreter, `uvx`, bridge version, and compilation state. Another open project is not a valid substitute. |
| Controller/project lock is busy | Stop overlapping bot/CLI operations or wait for the owner. Lock file existence alone is not ownership; do not delete an active lock or kill Unity indiscriminately. |
| Android build fails | Check Android modules, license, disk space, target switching, and build logs. A successful C# compile is not APK evidence. |
| Tests cannot write temporary files | Check Windows permissions/security software. Resolve the environment issue and rerun; filesystem errors are not model-quality evidence. |

A model call has a whole-request deadline, but a stage can involve multiple bounded calls, framework work, imports, and tests. There is no guarantee that a complete job finishes within one request timeout. SDK downloads, licensing prompts, unavailable providers, and external tools can still block progress.

## Project structure

```text
main.py                     CLI for the persistent supervisor
telegram_bot.py             Telegram transport and authorization
start_bot.ps1               Windows launcher with controller-lock checks
studio_models.json          task model preferences
requirements.txt            pinned direct Python dependencies
.env.example                public placeholders and configuration explanations
studio/
  supervisor.py             five-agent registration and job lifecycle
  agents.py                 Designer, Developer, QA contracts/workflows
  assets.py                 Art Designer, imports, caches, procedural options
  api.py                    metered model gateway and bounded fallback
  registry.py               provider catalogs, eligibility, benchmark cache
  model_selection.py        deterministic hybrid value ranking
  costs.py                  limits, reservations, settlement, cost reporting
  flexible.py, schemas.py   new and legacy typed contracts
  coordination.py           dependencies and persisted handoffs
  frameworks.py             CrewAI and AutoGen metered adapters
  providers.py, openrouter.py  credentials, provider errors, allowance parsing
  unity.py, unity_live.py    batch/live Unity execution and evidence
  mcp_client.py, mcp_setup.py  restricted bridge and project setup
  generation.py, scenes.py   implementation and scene-generation orchestration
  verification.py           saved-project verification
  state.py, artifacts.py, failures.py  persistence, packaging, diagnostics
  request_validation.py      simple noise filtering
  templates/                maintained C# runtime, Editor builders, Unity tests
generated_game/              required Unity seed, not published generated games
  Assets/Settings/           URP/input settings and required Unity metadata
  Packages/                  Unity dependency manifest and lock
  ProjectSettings/           seed project configuration
jobs/README.md              documentation for ignored local jobs
tests/                      Python regressions and deterministic game fixtures
tools/                      test runner, explicit Unity audits, Android helper
```

The release excludes local virtual environments, Codex/test scratch directories, reports and caches, generated job projects, legacy demo scripts/scenes, Unity import/build outputs, APKs, signing material, and `.env`. Required seed assets and their `.meta` files remain versioned. Exclusion from Git does not delete a developer's existing local games or backups.

## Terms used in this project

| Term | Meaning here |
| --- | --- |
| LLM | Large language model: a system that predicts/generates text, including code and JSON; its output can be wrong. |
| API | An interface software calls to request a service. Provider inference APIs and Telegram's API are different services. |
| Model | A particular system/version used for a task; an ID alone does not establish coding quality. |
| Provider | The service serving inference. OpenRouter can route a model to an underlying provider. |
| Tokens | The chunks of text used for model input/output limits and billing; tokens are not the same as characters. |
| Context window | The amount of input and output a model can accommodate in a request. Large capacity is not a quality score. |
| Reasoning tokens | Output allowance used for a model's internal reasoning; they can consume budget before the final answer appears. |
| Agent | A specialist role/workflow in the studio, not an unlimited autonomous process. |
| MCP | Model Context Protocol: a standard tool/resource interface. Here it connects trusted orchestration to a local Unity bridge. |
| Unity scene | A saved collection of game objects, components, references, cameras, and other game state. |
| Sprite | A 2D image imported for use in a game, often with transparency and a pixels-per-unit setting. |
| APK | Android application package. Building one does not establish installation, device usability, or store readiness. |
| QA | Quality assurance: checks and review intended to find defects. Automated QA covers only what it actually tests. |
| Rate limit | A provider's restriction on request frequency or quota, distinct from the studio's dollar cap. |
| Fallback | A bounded alternative model/provider route or supported mode after a failure; not permission to ignore account limits. |
| Benchmark | A measured result on a particular evaluation. Broad model benchmarks do not prove success on this game's requirements. |
| Caching | Reusing saved data or assets to avoid repeated work. Studio asset/catalog caches differ from provider-side prompt caching. |
| Budget reservation | A conservative amount held in the local ledger before a request. It is not a prepaid purchase or necessarily an actual charge. |
| Contract | The typed shape and rules expected for generated data; valid JSON can still violate the contract. |
| Probe | A requirement-linked sequence of gameplay actions and assertions run in Unity. |
| EditMode / PlayMode | Unity tests against Editor/scene structure and against running gameplay, respectively. |

## Security, sharing, and reproducibility

Run this as a local, single-user development tool. The Telegram user check and MCP tool restrictions reduce accidental access; they do not turn it into a safe hosted service for untrusted users. Generated C# runs inside Unity with that process's privileges. API deny-lists and scope checks are not a hostile-code sandbox.

The metered adapters share the selected provider gateway rather than owning independent keys. Unity and MCP child processes are launched with the configured OpenRouter, Dahl, and Telegram secrets removed from their inherited environment. This does not make prompts or generated archives anonymous: review all output before sharing it and check the policies of the external services you choose.

Keep `.env`, private keys, keystores, user-owned libraries, prompts, job records, local bridge configuration, and generated deliverables out of commits. If a real credential is exposed, revoke/rotate it with its issuer; removing its current file is not enough. Never paste raw provider headers or bot-token URLs into issues.

A clean working tree and ignore rules do not sanitize existing Git history. Release preparation found machine-specific path strings in older reachable blobs and author metadata in commits. No history rewrite is performed automatically. Review that history before making an existing repository public; do not assume this cleaned source tree removes older data from a push.

Pinned direct requirements, Unity package locks, the Editor version, saved source, and test evidence make a particular checkout easier to examine. They do not freeze the provider catalog, account allowance, model behavior, external package servers, GPU/Editor state, or all transitive Python dependencies. There is no application-wide lockfile for the complete Python dependency graph.

This checkout does not currently include a `LICENSE` file. Choosing a distribution license remains a task for the repository owner. Dependencies and imported assets have their own licensing terms.

## Conclusion

This project explores whether AI agents can produce complete, high-quality games using carefully selected, affordable models. The approach is promising, and the pipeline has produced useful prototypes, but our experiments also showed that it is not necessarily the most cost-efficient way to develop games, despite that being our original goal. Repairs, validation, assets, infrastructure, and human review all contribute to the real cost. The useful result is a system whose decisions and failures can be inspected, and whose ambition is kept separate from what its evidence actually proves.
