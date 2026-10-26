# Knowledge Vault × ChainSWE — cost demo

A small web UI over the harness that shows what cross-session memory costs and saves on
ChainSWE, with a toggle between **Mem0** (`mem_factsall`: official mem0 extraction prompts,
full injection) and **Knowledge Vault** (`mem_fusednote`: one structured note per session),
with the no-memory `seq` arm as reference.

Everything shown is computed by the harness's own code paths: resolve from `results.jsonl`
(`harness/grading.py`), tokens from the per-call usage via `harness.ledger.summarize`, USD via
`harness.ledger.cost_usd` at one row of `harness/prices.json`.

## Run it

```bash
# deps: the harness venv (mini-swe-agent 2.4.5, pyyaml) + fastapi uvicorn
VENV=/scratch/$USER/venvs/kv-demo          # or any venv with the deps
cd <repo root>
RSM_SIF_DIR=/scratch/$USER/kv/sifs $VENV/bin/python demo/server.py        # http://127.0.0.1:8765
# or: $VENV/bin/uvicorn demo.server:app --port 8765
```

Reaching it:

- From a VS Code / code-server session on the same node (OnDemand): open the port through the
  session's proxy — `echo "${VSCODE_PROXY_URI//\{\{port\}\}/8765}"` prints the URL, e.g.
  `https://ood.aicr.ai/rnode/<node>/<port>/proxy/8765/` (keep the trailing slash; the page uses
  relative URLs so any path prefix works). The server must run on the node that hosts the
  code-server job.
- Otherwise tunnel the port: `ssh -L 8765:127.0.0.1:8765 <node>`.

| env | default | meaning |
|---|---|---|
| `RSM_RUNS_DIR` | `runs/` | where run-ids live (recorded runs + live runs) |
| `RSM_SIF_DIR` | `sifs/` | Apptainer images named `<docker tag>.sif`, needed for live runs only |
| `DEMO_RUN` | `v3main100` | run-id shown first |
| `DEMO_ARMS` | `{"none":"seq","mem0":"mem_factsall","kv":"mem_fusednote"}` | which arm directory each toggle position reads |
| `RSM_API_BASE` / `DEMO_SERVED_NAME` | `http://127.0.0.1:8000/v1` / `coder-next` | prefilled live-run endpoint |
| `DEMO_HOST` / `DEMO_PORT` | `127.0.0.1` / `8765` | bind address |

## What the page shows

`/` is laid out like a coding agent:

- **Header** — run, the memory-system toggle (No memory / Mem0 / Knowledge Vault), the saving
  chip (Knowledge Vault vs Mem0 USD per chain over the run's complete chains), price row, and a
  link to `/report`.
- **Left: sessions** — every chain in the run with, for the selected system, resolved count and
  USD; a chain expands into its tasks (sessions) with ✓/✗, steps and USD. Filter box on top.
- **Middle: the session as a chat** — the memory injected before the task (Knowledge Vault note
  lines rendered with their DID / WHERE / VERIFY / CONTEXT / LESSON tags, or Mem0 facts), the issue,
  then each agent step (thought, `$ command`, tool output with return code), the submitted patch,
  the official grading (F2P / P2P tests), and what was written to memory afterwards. The chat box
  at the bottom asks the local actor model about the session (`POST /api/ask`, context = the
  transcript); **▶ Run this chain live** starts a new harness run for the chain.
- **Playback bar** (under the session header) — ▶ Play replays the session step by step at
  emulated real-time speed: each thought is typed out, the command appears, a "running" pause
  stands in for its execution, then the tool output; after the last step come the grading phase
  and the memory-write phase; with *continue to next task* on, the chain plays through all its
  tasks (so the note written after task k is seen being injected into task k+1). ⏭ reveals one
  step, ⏮ restarts, ⏩ shows everything; speed 0.5–10× (space bar toggles play). *Right panel
  follows* opens the file a command touches and ends on Changes / Memory.
  Pacing: the session's measured wall time (`secs` in `results.jsonl`) is spread over its steps
  in proportion to the tokens each step generated, plus a short per-command pause; the writer phase
  lasts its measured `mem_write_secs`. Per-step latencies are not in the logs (the ledger books
  actor calls at session end), so this emulates the recorded timing rather than replaying it.
- **Right: files** — *Changes* (the agent's final `git diff`, per file), *Files* (paths the agent
  read or edited; opens the file from the task image when the SIF is present, otherwise what the
  agent saw), *Memory* (before / after), *Issue*, *Ledger* (every call with prompt / cached /
  completion tokens and the priced USD).

### Interactive use (the composer at the bottom of `/`)

The box has three modes: **Ask about session** (questions answered from the open transcript),
**Chat with model** (free chat on the selected endpoint; the reply shows model, tokens and latency),
and **Run a task** (sandbox mode). The endpoint picker next to it (local vLLM / GoodVision
SmartRouter test / prod) and the model field apply to all three and to the live-run buttons.

**Your sandbox** (left-panel toggle) is the "specify your own task" path: create a *project* from a
sandbox image — any ChainSWE repo image under `RSM_SIF_DIR` (the repository at the chain's base commit)
or a generic `sandbox-*.sif` (e.g. `sandbox-python-git`, a plain Python image with git; `/work` is an
empty git-initialised workspace) — then type tasks. Each task runs through `demo/custom_runner.py`,
which is the harness's own stack (RepoEnv in Apptainer, mini-swe-agent with the pinned templates,
the registered memory layers, per-task harness commit, token ledger) minus grading: a free-form task
has no F2P/P2P tests, so sessions are "not graded". Each memory system keeps its own persistent
workspace and memory store per project, so task k+1 inherits the repo state and the notes of task k —
the same seq protocol as ChainSWE. Tick KV / Mem0 / none to run a task on several arms at once; the
chips under each task open the resulting sessions, and `/compare` can be pointed at the project
(run `custom`, chain = project name). Pull more images with
`apptainer pull --name sandbox-<name>.sif docker://<image>` into `RSM_SIF_DIR`.

Demo runs set `RSM_VALID_V2=1` (the writer validator's v2 rule from `memory/ours/fusednote.py`), so a
note that merely mentions the tool name is not discarded.

### Showcase chain

`pyccel_pyccel_20240307` (recorded paper run `v3main100`, Qwen3.8-27B actor) is the default chain in
both pages because it is the one chain of 100 where Knowledge Vault beats **both** Mem0 and no-memory
on resolve rate *and* cost: KV 2/3 resolved at $3.15 vs Mem0 1/3 at $9.77 and no-memory 1/3 at $12.95
(opus5 price row). Task 3 is the story: with 2.1K chars of notes injected, KV fixes it in 20 steps
($0.97); Mem0 injects nothing (its extractor produced no facts), burns the 100-step budget ($7.24) and
fails. Direct link: `compare?a=kv_v3&b=mem0_v3&c=none_v3&cid=pyccel_pyccel_20240307`.
Twelve more chains have KV ahead of Mem0 on both axes (e.g. `getmoto_moto_20220601`: 3/3 vs 2/3,
$3.10 vs $14.81; `copier-org_copier_20240418`: 3/3 vs 2/3, $2.37 vs $3.72). `showcase` in
`demo/configs.json` records the pick; `DEMO_DEFAULT_CID` overrides the default chain.

### Memory-quality examples (both systems injected memory)

Cases where Mem0 *did* inject memory in every later task and Knowledge Vault still resolved more at
lower cost (6 of 200 chains, none decided by a loop; recorded v2main100 configs `kv_v2 / mem0_v2 /
none_v2`):

- `compare?a=kv_v2&b=mem0_v2&c=none_v2&cid=pyccel_pyccel_20240307` — task 3: both arms injected
  ≈2.3K chars. KV's note = concrete repo knowledge (files, line numbers, how method dispatch works,
  verify commands) → ✓ 45 steps / $4.38; Mem0's facts = statements about the assistant ("prefers to
  follow existing patterns…") → ✗ 54 steps / $6.07; no memory ✓ 63 steps / $7.10. Chain: KV 2/3
  $14.89, Mem0 1/3 $22.58, none 2/3 $20.79.
- `compare?a=kv_v2&b=mem0_v2&c=none_v2&cid=matthewwithanm_python-markdownify_20250302` — task 3:
  KV ✓ 20 steps / $1.76 with 2.1K chars (where `convert_br`/`_inline` live, which test files);
  Mem0 ✗ 33 steps / $4.78 with **19.8K chars** of assistant-personality facts; no memory ✗ 15 steps
  (no patch). KV resolved a task neither of the others did. Chain: KV 2/3 $5.38, Mem0 1/3 $7.84, none
  1/3 $5.22.

### Holistic comparison: memory × model endpoint (`demo/configs.json`)

`/compare` compares **configurations** = memory system × model endpoint, e.g. *Knowledge Vault +
GoodVision SmartRouter (auto)* against *Mem0 + GPT-5.5 (fixed model)*, with optional third column
(ablations such as *Knowledge Vault + GPT-5.5* or *Mem0 + SmartRouter* isolate the memory effect from
the routing effect). Presets live in `demo/configs.json`; each configuration's runs are stored under
run id `cfg__<provider>__<model>` with the memory system as the arm directory, so chains can be run
per configuration and compared later. "custom: …" in a column lets you combine any memory system,
endpoint and model id ad hoc.

Each column shows one line of numbers (cost, steps, tokens, resolved, emulated time) and a **step
chain**: one dot per agent step, one row per task — green = the command returned 0, red = non-zero exit
or a format error, blue = the submit step, grey = not reached yet — filled in as the replay runs, with
the task's ✓/✗ at the end of the row. Hover a dot for the command and exit code. **Click a dot to jump
the replay there** (everything before it is revealed and counted, the transcript scrolls to it, ▶ Play
resumes from that step; jumping backwards rewinds the task's counters); the task label `T2` jumps to
the start of that task, with earlier tasks credited from their recorded totals. Dots not yet reached are
invisible until you hover the row, so the chain still grows with the replay. Two honesty tags sit
on each row: **↻ loop ×N** when the agent repeated one command N ≥ 5 times (hollow red dots) — a
decoding loop of the actor, not a memory effect — and **no mem** / **1.2K mem** for what was injected.

Read the showcase this way: Mem0's `pyccel` task 3 is 81 identical `grep` calls until the 100-step
limit, with *nothing* injected (its extractor stored zero facts for the whole chain); the no-memory
arm solved the same task in 37 steps. The memory-retrieval effect is KV's 20 steps / $0.97 vs the
no-memory 37 steps / $1.70 (both resolved) — the note sent the agent straight to the right files.
Across v3main100, loops ≥ 10 identical commands occur in 12 KV, 20 Mem0 and 22 no-memory sessions
(of 304 each), and Mem0 injected nothing in 104 of 204 later tasks.

**Cost is priced per call by the model that actually served it** (the router reports the routed model,
e.g. `gpt-5.2-2025-12-11`, in every response and the ledger stores it), using `demo/model_prices.json`
(list prices; Anthropic rows verified on platform.claude.com 2026-10-22, OpenAI rows from price
aggregators because openai.com blocks automated fetches — marked `verified: false`; the router's usage
page is the authoritative bill). The "served by" column shows which models the SmartRouter chose.
Switch to "one global row" to price every configuration at the same rate, as the paper's tables do.

`/compare` runs **two methods side by side on the same chain** (A vs B, default Knowledge Vault vs
Mem0): each column replays its sessions with the same emulated-real-time player (`static/player.js`),
with live counters above each transcript — steps, actor tokens, writer tokens, USD at the chosen
price row, emulated time — a progress bar against the chain's recorded total, and a footer with the
running difference ("Knowledge Vault −41% · $1.35 vs $2.31 …") plus the whole-chain recorded totals.
"Play the whole chain" lets each side continue through its tasks on its own clock, so the cheaper
method visibly finishes first. **▶ Run both live** starts the two harness runs for the chain on the
selected model endpoint (local vLLM or the GoodVision SmartRouter, see below) into a fresh run id
and switches to it when both finish. `?run=<id>&cid=<chain>` deep-links a comparison.

### Model endpoints (`demo/providers.py`)

| provider | endpoint | key |
|---|---|---|
| `local` | vLLM from `demo/slurm/serve_actor.sbatch` (or `RSM_API_BASE`) | none |
| `goodvision-test` | `https://api-test.goodvision.tech/v1` | `GOODVISION_API_KEY` |
| `goodvision-prod` | `https://api.goodvision.tech/v1` | `GOODVISION_API_KEY` |

Put the key in `demo/.env` (`cp demo/.env.example demo/.env`, git-ignored). `GET /api/providers`
reports each endpoint's live status with the router's own error code (`verified_identity_required`
until a key is set, `invalid_api_key` for a bad one) and lists the model market once the key works;
the UI disables live runs until then. What is known about the GoodVision API, and what remains to
verify once we have access, is in `demo/goodvision.md`. The harness changes it relies on are
env-gated (`RSM_API_KEY`, `RSM_OPENAI_STRICT`, `RSM_WRITER_API_KEY`, `RSM_FACTS_API_KEY`), so local
runs are unchanged.

`/report` is the aggregate dashboard: hero saving, KPI tiles, cumulative-cost line, per-chain
diverging bars, arms table, chain explorer, live-run panel.

**Live runs** launch `harness/mini_swe_agent.py` for one chain with `RSM_LEDGER=1` under the v3.0
protocol env (TEAM_GUIDE §1) against an OpenAI-compatible endpoint; the mem0 extraction is pointed
at the same endpoint (`RSM_FACTS_API`), so no external API is used. Results land in
`runs/<run id>/` (default `demo_live`). The endpoint is prefilled from `RSM_API_BASE`, else from
the file `demo/slurm/serve_actor.sbatch` writes (`/scratch/$USER/kv/actor_endpoint`).

## Token sources (shown per session as `tok.src` / `writer_src`)

- `ledger` — `calls.jsonl` written by `harness/ledger.py` (live runs): exact, every role.
- `traj` — actor usage from `traj_<k>_*.json` (recorded v2/v3 runs, which predate the ledger).
  The writer side is then **estimated** (`writer_src = estimate`) from the material the harness
  handed the writer, with the ledger's own chars→tokens rule: one fused-note distill call for KV,
  the two official extraction branches over `material[:23000]` for mem0. The UI marks these.
- The vLLM used for the recorded runs did not report prefix-cache hits, so cache-aware = raw there.

## Live-run prerequisites

- An Apptainer image per chain: `apptainer pull --name <tag>.sif docker://<docker_image>` into
  `RSM_SIF_DIR` (`tag` = the part after `:` in `docker_image` of `data/chainswe_data.jsonl`).
- A vLLM server with the actor model, e.g. `vllm serve Qwen/Qwen3.8-27B-FP8 --served-model-name coder-next`.
- `apptainer` on the node that runs the demo server (the harness execs the image for every step).
