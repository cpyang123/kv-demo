# Knowledge Vault on ChainSWE

This repository holds two things that share one codebase:

1. **The ChainSWE evaluation harness** (`harness/`, `memory/`, `data/`, `analysis/`). It runs a coding agent
   (mini-SWE-agent) through *chains*: three to five related GitHub issues of one repository, solved one after another in
   the same workspace. A pluggable *memory layer* decides what the agent carries from one task to the next.
2. **The Knowledge Vault demo** (`demo/`, `tests/`). A FastAPI web app that replays the recorded runs, compares memory
   methods side by side, and lets a visitor run the agent live on a benchmark chain or on their own project.

**Knowledge Vault** is the team's memory method: after each task a separate writer call distils the session into a short
structured note, and the next task starts with those notes. The demo shows that this needs fewer tokens than Mem0, MemOp,
pasting the full history, or no memory at all.

Coding agents: read [AGENTS.md](AGENTS.md) first. Deployment: [demo/deploy/README.md](demo/deploy/README.md).

## Quick start

```bash
# the demo (replays work out of the box; live runs need Apptainer images and a model endpoint)
python -m venv venv && venv/bin/pip install -r demo/requirements.txt
RSM_SIF_DIR=/path/to/sifs venv/bin/python demo/server.py            # http://127.0.0.1:8765

# backend unit tests (offline, about 20 s)
venv/bin/pip install -r demo/requirements-dev.txt && venv/bin/pytest -q tests/test_demo.py

# one harness run without the demo
python harness/mini_swe_agent.py --arm seq --chains-file data/chains/q38_chains_s0.txt --run-id myrun
RSM_API_BASE=http://127.0.0.1:8470/v1 \
python harness/mini_swe_agent.py --memory-system fusednote --chains-file data/chains/q38_chains_s0.txt --run-id myrun
```

Results land in `runs/<run-id>/<chain>__s0__<arm>/` (see *Run directories* below).

## Repository map

```
data/                  the benchmark
  chainswe_data.jsonl    100 chains: repo, docker image, and each task's issue text, tests and reference patch
  task_link_flags_v2.json  which later tasks build on an earlier one
  chains/                chain-id lists used as --chains-file (splits, pilots, the 100-chain main set)

harness/               the agent pipeline
  mini_swe_agent.py      entry point: runs chains for one arm or memory system, grades, commits, writes results
  runner.py              thin alias (python -m harness.runner)
  config.py constants.py paths, prompt templates, arm names, every magic number
  env.py                 RepoEnv: runs the agent's shell commands inside an Apptainer image
  grading.py             the single source of truth for pass/fail (FAIL_TO_PASS / PASS_TO_PASS tests)
  ledger.py prices.json  token ledger: records every model call (agent and memory writer) and prices it
  gateway_model.py       strips the conversation to role/content for strict OpenAI-style gateways (RSM_OPENAI_STRICT=1)
  live_agent.py          saves the trajectory after every model reply, so the demo can stream a live run
  mem_snapshot.py        saves the memory store after every write (the demo's memory view)
  inline_note.py boundary_persist.py   the "inline" Knowledge Vault variant: the agent writes its own note as its last step

memory/                memory layers (plugins)
  base.py                the MemoryLayer contract and the registry (@register)
  ours/fusednote.py      Knowledge Vault and its versions (fusednote = the paper's; v4knote, v5knote, …) + prompts/
  ours/inline_note.py    Knowledge Vault v5 inline
  baselines/             mem0_official.py (Mem0), memop_prompt.py (MemOp), reasoningbank.py, cline_membank.py + prompts/
  attribution/           frozen attribution probes used in the paper's analysis

analysis/              main_format_table.py reproduces the paper's main table from runs/

demo/                  the web demo
  server.py              the FastAPI app: every /api route, run loading, live runs, sandbox projects, uploads
  custom_runner.py       runs one sandbox task (your own project) through the harness
  providers.py           model endpoints: local vLLM, GoodVision SmartRouter (test / production), Azure OpenAI
  scrub.py               removes API keys that mini-swe-agent writes into trajectory files
  configs.json           comparison presets and the example ("showcase") chains with their titles and blurbs
  task_summaries.json    one short plain summary per task (304 tasks), shown instead of raw issue titles
  model_prices.json      list prices per model, for live runs
  static/                index.html (Agent), compare.html + player.js (Compare), report.html (Report), theme.css
  deploy/                Docker entrypoint, Caddyfile, systemd unit, GoodVision check, VM setup
  goodvision.md          everything known about the GoodVision router API
  README.md              the demo's features, settings and API in detail

tests/
  test_demo.py           backend unit tests (offline; a scratch copy of the tree, fake endpoints)
  test_live_agent.py test_inline_ledger.py   harness unit tests
  ui/                    Playwright browser suites + replay_server.py and canned_model.py (see tests/ui/README.md)

Dockerfile docker-compose.yml infra/ .github/workflows/   image, services, Azure Bicep, CI and deploy workflows
```

`runs/` (recorded results), `sifs/` (Apptainer images) and `demo/live/` (job logs) are data, not code.

## How a chain run works

`harness/mini_swe_agent.py` takes an *arm* (`--arm seq` = no memory, plus `oracle` / `indep` controls) or a memory system
(`--memory-system <name>`, any layer registered in `memory/`). For each chain it:

1. opens the chain's repository image (`RepoEnv`, Apptainer) at the chain's base commit;
2. for each task: asks the memory layer to `wrap` the issue text (inject what it remembers), lets the agent work with
   shell commands until it submits or hits the step limit, grades the result with the task's tests, commits the change
   (`rsm: task <k>`), then asks the memory layer to `write` what should be remembered;
3. writes one row per task to `results.jsonl`, the trajectories, and the token ledger.

Semantics worth knowing: steps are model calls; the agent may not commit (a guard reverts it); a task with nothing to grade
is marked `gradable: false`; an infrastructure failure aborts the chain instead of recording a fake result.

### Run directories

```
runs/<run-id>/<chain-id>__s0__<arm>/
  results.jsonl                 one row per task: resolved, steps, exit status, memory stats
  traj_<k>_<instance>.json      the agent's conversation for task k
  calls.jsonl                   the token ledger (every model call: who, prompt/completion tokens)
  mem_store_<k>.json            the memory store after task k (only when RSM_MEM_SAVE=1, e.g. live runs)
```

Arm directory names: `seq` (no memory), `mem_fusednote` (Knowledge Vault), `mem_factsall` (Mem0), `mem_memop` (MemOp),
`mem_rawcat` (full history). Run ids: `v3main100` / `v2main100` are the paper's recorded runs; `live_<timestamp>` are
live runs started on the Agent page; `cfg__<endpoint>-<model>` are runs started on the Compare page; `custom` holds
sandbox projects.

### Adding a memory method

Write one file in `memory/baselines/`: a `MemoryLayer` subclass with `@register`, implementing
`write(spec, material, delta, env)` and optionally `wrap(question, env)`. Nothing else changes:
`--memory-system <name>` runs it. The contract is in the docstring of `memory/base.py`.

## The demo

Three pages, all served by `demo/server.py` from `demo/static/`:

| page | file | what it is |
|---|---|---|
| Agent `/` | `index.html` | Opens on *Your sandbox → New project*: give the agent a task on an empty folder, your files, a git repo or an example repo, and watch it work. *ChainSWE chains* replays an example chain and runs it live. |
| Compare `/compare` | `compare.html`, `player.js` | Memory methods side by side on one chain, saved live run or sandbox project: transcripts, a memory view, tokens and cost. |
| Report `/report` | `report.html` | The 100-chain results: cost and tokens per method, and a chain explorer. |

The method names on screen map to harness arms: Knowledge Vault = `kv`, Mem0 = `mem0`, MemOp = `memop`,
Full history = `rawcat` (recorded only), No memory = `none`.

The server keeps no database: everything it shows is read from `runs/` and cached in memory; running jobs live in the
in-memory `JOBS` table and are lost on restart (their results stay on disk). Live runs and sandbox tasks are subprocesses
of the harness. `demo/README.md` documents every setting, API route and page in detail.

## Tests

| what | how | needs |
|---|---|---|
| backend | `pytest -q tests/test_demo.py` | nothing (offline) |
| harness units | `pytest -q tests/test_live_agent.py tests/test_inline_ledger.py` | nothing |
| browser | `node tests/ui/ui_*.js` against `tests/ui/replay_server.py` | Playwright, Apptainer for the sandbox suite |

The browser suites never call a paid model: `replay_server.py` replays recorded sessions as "live" runs and
`canned_model.py` is a scripted model endpoint. Details: `tests/ui/README.md`.

## Further reading

- `demo/README.md`: the demo in depth. `demo/deploy/README.md`: deployment paths. `demo/goodvision.md`: the router.
- `TEAM_GUIDE.md`, `MIGRATION.md`, `AUDIT-2026-08-26.md`: the research team's notes on the harness (partly in Chinese).
