# AGENTS.md: context for LLM coding agents

Read this before changing anything. It says what the repository is, where things live, how to verify a change, and which
mistakes are expensive. Human-oriented overview: [README.md](README.md). Demo details: [demo/README.md](demo/README.md).
`CLAUDE.md` is an older one-off handoff about deploying on one particular VM; this file is the general guide.

## What this repository is

A research codebase plus its demo.

- **Research claim.** A coding agent that solves a *chain* of related GitHub issues in one repository should get cheaper
  as it goes, if it remembers the right things. **Knowledge Vault** (the team's method) has a writer model distil each
  finished task into a short structured note; later tasks start with those notes. On the ChainSWE benchmark (100 chains,
  304 tasks) it uses fewer tokens than the alternatives while resolving about as many tasks.
- **Harness** (`harness/`, `memory/`, `data/`): runs mini-SWE-agent through chains inside Apptainer images, with a
  pluggable memory layer, test-based grading and a token ledger.
- **Demo** (`demo/`): a FastAPI server and three static pages (Agent, Compare, Report) that replay recorded runs, start
  live runs, and run tasks on a visitor's own "sandbox" project.

## Vocabulary

| term | meaning |
|---|---|
| chain (`cid`) | 3 to 5 tasks of one repo, in order, e.g. `Python-Markdown_markdown_20191125` |
| task / session | one issue (`order` 1..n, `instance` = its SWE-bench id) / the agent's run on it |
| arm | a directory name for one method: `seq`, `mem_fusednote`, `mem_factsall`, `mem_memop`, `mem_rawcat` |
| kind | the demo's name for a method: `none`, `kv`, `mem0`, `memop`, `rawcat` (maps to the arms above, in that order) |
| run id | a directory under `runs/`: `v3main100` (paper), `live_<ts>` (Agent page), `cfg__<api>-<model>` (Compare), `custom` (sandbox) |
| actor / writer | the agent's model calls / the memory method's own model calls (both are in the ledger) |
| showcase chains | the example chains the pages offer (`demo/configs.json`), where Knowledge Vault wins against every other method |
| sandbox project | a visitor's own project: `seed/` (their files or clone) plus one workspace per method |
| SIF | an Apptainer image; one per chain (`<docker tag>.sif`) plus generic ones (`3.12.sif`) for sandbox projects |

## Where to change what

| you want to change | file |
|---|---|
| an API route, run loading, live runs, sandbox projects, uploads | `demo/server.py` (sections are marked with `# ----- <name>` banners) |
| how one sandbox task runs (rewind, memory store, commits) | `demo/custom_runner.py` |
| a model endpoint or its health check | `demo/providers.py` |
| the Agent page | `demo/static/index.html` (one file: CSS, markup, script) |
| the Compare page | `demo/static/compare.html`; the transcript player and live feed shared by all pages: `demo/static/player.js` |
| the Report page | `demo/static/report.html` |
| colours, focus rings, the shared top bar look | `demo/static/theme.css` (loads after each page's own styles) |
| the example chains, their titles and blurbs, the default chain | `demo/configs.json` (`showcase_chains`, `showcase_default`) |
| a task's one-line summary | `demo/task_summaries.json` (`{cid: {order: text}}`, re-read on change) |
| a memory method | `memory/ours/` or `memory/baselines/`; the contract is in `memory/base.py` |
| grading, the agent loop, the ledger | `harness/grading.py`, `harness/mini_swe_agent.py`, `harness/ledger.py` |

`player.js` is imported by all three pages. Keep its exports stable and load every page after changing it.

## How to verify a change

1. **Backend:** `pytest -q tests/test_demo.py` (offline, about 20 s). Add a test next to the existing ones for any
   server change.
2. **Pages:** the Playwright suites in `tests/ui/` (`ui_index.js`, `ui_sandbox.js`, `ui_compare.js`, `ui_memory.js`,
   `ui_stop.js`, `ui_report.js`). Run them against `tests/ui/replay_server.py`, which fakes live runs from recorded
   sessions, with `tests/ui/canned_model.py` as the model. Both are free. `tests/ui/README.md` has the commands.
3. **Look at it.** Take a screenshot at 1500 px, 420 px and in dark mode and read it. Suites catch regressions, not
   ugliness.
4. Static files go live on a browser reload; `.py` changes need a server restart.

Against a server with real model keys, only read: install
`page.route('**/api/**', r => r.request().method() === 'GET' ? r.continue() : r.abort())` before navigating.

## Rules that prevent expensive mistakes

- **Never start a paid run to test something.** A live chain run costs dollars of router credit; a sandbox task costs
  cents. Use the replay server and the canned model. Ask the owner before any paid call.
- **Never send `temperature` to GoodVision** or other strict gateways, and send `max_completion_tokens` there instead of
  `max_tokens`. The switch is `RSM_OPENAI_STRICT=1`; see `memory/ours/fusednote.py` and `memory/baselines/memop_prompt.py`
  for the pattern. The local (vLLM) request bodies must stay byte-identical, so recorded runs stay reproducible.
- **Secrets** live only in environment files outside the repo. Never read, print or commit keys. `demo/scrub.py` removes
  keys from run artifacts; keep it in the path of anything that writes trajectories.
- **Recorded runs are data.** Do not rewrite `runs/v3main100` or `runs/v2main100`; numbers in the paper come from them.
- **User input reaches the filesystem and git.** Project names, upload paths, zip members and git URLs are validated in
  `demo/server.py` (no `..`, no symlink following, no `.git` writes, public https hosts only). Keep every new path
  through the same helpers (`_upload_parts`, `_check_target`, `_write_everywhere`).
- **Escape everything** a page puts into `innerHTML` (`esc()`); task text, project names and model names are user data.
- **Honest numbers.** The pages never claim a saving that the data does not support: a method that used fewer tokens
  than Knowledge Vault is shown as such, failed runs are left out of comparisons, and columns on different models are
  not compared by percentage. Do not "fix" a red number by hiding it.

## Product direction (why the pages look the way they do)

The demo is for people who have not read the paper. The owner's standing requests:

- The Agent page opens on *Your sandbox → New project*, like a coding assistant. Expert options live in the ⚙ panel.
- The memory switcher exists only in *ChainSWE chains* mode, above the example chain card. In the sandbox, methods are
  picked under "Run with".
- Compare: Knowledge Vault is the left column, No memory the right-most. Each column chooses only its API and model.
  ⚙ → *What to compare* picks an example chain, a saved live run or a sandbox project.
- Tasks are named by their short summary, never by a raw issue title. Instance ids show only with *Show details*.
- Plain words, nothing shown twice, no option groups in selects, consistent top bar on every page. The logo is the
  home link.

## Things that look like bugs but are not

- `tok.usd` on a recorded session is priced at a *price row* (recorded sessions carry no model id); live runs are priced
  per model. The price row is currently process-global on the server (`_set_price`), a known limitation.
- The memory store of a recorded run is reconstructed from what was injected into the next task; only live runs save
  exact snapshots. The last task's write may therefore be "unknown".
- A chain without its SIF on the host cannot run live and shows a best-guess file tree. That is an ops matter, not a
  page defect.
- Jobs disappear from `/api/live` after a server restart; their results stay under `runs/`.

## Conventions

- Python: standard library plus FastAPI; long lines are normal in this codebase; comments explain *why*.
- Pages: one HTML file per page with inline CSS and script, no build step, no framework.
- Tests state the user-visible behaviour in their names (`check('↺: every column rewinds to the start of task 1', …)`).
- Docs that must stay current when behaviour changes: `demo/README.md`, `tests/ui/README.md`, this file.
