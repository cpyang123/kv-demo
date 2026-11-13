# Knowledge Vault × ChainSWE — token and cost demo

A web demo of the Knowledge Vault paper. A coding agent works through a *chain* of related tasks on one repository
(ChainSWE); after every task a memory method carries what the agent learned to the next task. **Knowledge Vault** (ours,
arm `mem_fusednote`) writes one short note per task, so later tasks need far fewer tokens than with the other methods:
**Mem0** (`mem_factsall`: the official mem0 extraction prompts, full injection), **MemOp** (`mem_memop`: one guidance
document rewritten after every task), **Full history** (`mem_rawcat`: earlier sessions pasted in verbatim) and
**No memory** (`seq`). Knowledge Vault, Mem0, MemOp and No memory also run live (`GET /api/meta` → `live_kinds`); Full
history exists only in the recorded paper run.

Everything shown is computed by the harness's own code paths: resolve from `results.jsonl` (`harness/grading.py`),
tokens from the per-call usage via `harness.ledger.summarize`, USD via `harness.ledger.cost_usd` at one row of
`harness/prices.json` (or per model, see *Cost* below).

| page | file | what it is |
|---|---|---|
| `/` Agent | `static/index.html` | a coding-agent view: your own tasks in a sandbox, or recorded and live ChainSWE chains, as a transcript |
| `/compare` | `static/compare.html` + `static/player.js` | one chain replayed side by side, one column per memory method |
| `/report` | `static/report.html` | the paper's results over the whole recorded run |

All three share `static/theme.css` and are served by `demo/server.py`. Static files go live on a browser reload; `.py`
edits need a server restart.

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
  `https://ood.aicr.ai/rnode/<node>/<port>/proxy/8765/` (keep the trailing slash; the pages use
  relative URLs so any path prefix works). The server must run on the node that hosts the
  code-server job.
- Otherwise tunnel the port: `ssh -L 8765:127.0.0.1:8765 <node>`.

| env | default | meaning |
|---|---|---|
| `RSM_RUNS_DIR` | `runs/` | where run ids live (recorded runs, live runs, sandbox projects under `custom/`) |
| `RSM_SIF_DIR` | `sifs/` | Apptainer images: `<docker tag>.sif` per chain (live runs, example repos) and generic images such as `3.12.sif` (sandbox) |
| `DEMO_RUN` | `v3main100` | recorded run shown first |
| `DEMO_ARMS` | `{"none":"seq","mem0":"mem_factsall","kv":"mem_fusednote"}` | which arm directory each live memory method reads |
| `DEMO_DEFAULT_CID` | (unset: `showcase_default` in configs.json) | the example chain the pages open on; used only when it names one of the listed showcase chains |
| `DEMO_TASK_SUMMARIES` | `demo/task_summaries.json` | the one-line task summaries (see *Task summaries*); re-read when the file changes |
| `RSM_API_BASE` / `DEMO_SERVED_NAME` | `http://127.0.0.1:8000/v1` / `coder-next` | the `local` endpoint and its model name |
| `DEMO_PROVIDER` | `goodvision-test` with a key, else `local` | endpoint selected when a page opens |
| `DEMO_HOST` / `DEMO_PORT` | `127.0.0.1` / `8765` | bind address |
| `DEMO_DISABLE_LIVE` | unset | `1` = replay-only deployment: live runs and sandbox tasks are refused (403), the pages hide them |
| `DEMO_CUSTOM_WORK` | `/scratch/$USER/kv/custom` | sandbox workspaces: `<project>/seed/` (your files or clone) and `<project>/<arm>/repo/` per memory method |
| `DEMO_LIVE_DIR` | `demo/live/` | job logs of live runs and sandbox tasks |
| `DEMO_GIT_TIMEOUT` | `180` | seconds a git clone of a new project may take |
| `DEMO_SEED_MAX_MB` | `500` | size limit of a project's files (a clone incl. `.git`, or the uploads) |
| `DEMO_UPLOAD_MAX_MB` | `50` | size limit per uploaded file (a zip: per member and the zip itself) |
| `DEMO_GIT_ALLOW_FILE` | unset | tests only: `1` also accepts `file://` URLs and local paths as git repos |
| `DEMO_PROVIDER_TTL` | `300` | seconds an endpoint check is reused; after that the stale result is served while one background probe refreshes it (a GoodVision check sends two tiny paid completions) |
| `AZURE_OPENAI_ENDPOINT` (or `AZURE_OPENAI_BASE_URL`) / `AZURE_OPENAI_API_KEY` | unset | Azure OpenAI resource (`https://<resource>.openai.azure.com`, any path) and key; enables the `azure` endpoint (v1 OpenAI-compatible API, bearer key) used by the GPT-5.4 presets |
| `AZURE_OPENAI_DEPLOYMENTS` | unset | model id → deployment name, e.g. `gpt-5.5=my-gpt55,gpt-5.4-mini`; unlisted GPT ids use their own name |
| `DEMO_GPT_VIA_AZURE` | `1` | with Azure configured, GPT ids asked of GoodVision (agent view, sandbox, chat) are served by Azure to save router credit when Azure has that deployment (checked once with a 16-token request; GPT-5.5 is not deployed on `chronosense` as of 2026-11-05, so it stays on the router); `auto` and Claude stay on the router; `0` turns it off |

## Agent (`/`)

Laid out like a coding agent: a list on the left, the session as a chat in the middle, files and numbers on the right.

**Top bar** — the brand, the **method switcher** (Knowledge Vault | Mem0 | No memory), the page links (Agent · Compare ·
Report) and ⚙. The switcher always means *which memory method's run of the open task you are looking at*, and it sits
in the same place in every state (sandbox draft, sandbox project, example chain, saved live run, memory view). A method
with nothing to show for the open task is dimmed and its tooltip says why; clicking a method never starts a run. In the
sandbox draft it picks the run you will watch once the task starts and stays in step with **Run with** (below): picking a
method in the switcher ticks it there, unticking the watched method moves the switcher to the first one still ticked.

**Left panel** — a toggle **Your sandbox | ChainSWE chains** (`?src=chains`), the list (a filter box appears above more
than eight projects) and « to collapse the panel.

### Your sandbox (the default)

A plain `/` opens a **New project** draft, like a new chat: choose what the agent starts from —

- **Empty folder** — an empty git-initialised workspace in the default generic image (`3.12`: Python 3.12 with git);
- **Your files** — files, a folder or a single `.zip` (unpacked; a lone top-level folder is stripped); drop them on the card;
- **Git repo** — a public `https://` repository, optionally a branch or tag; `github.com/owner/repo` without the scheme
  works too. The clone runs on the server (shallow, no prompts, `DEMO_GIT_TIMEOUT`, `DEMO_SEED_MAX_MB`); a URL with
  `user:token@` is used for the clone only and never stored;
- **Example repo** — a ChainSWE chain's repository at the commit its tasks start from (its chain image);

optionally name the project (otherwise it is named after the repository or the first words of the task), type the task in
the box at the bottom and press **Run ▶**. **Run with** under the box chooses the memory methods the task runs with
(Knowledge Vault and Mem0 ticked by default, No memory optional), like a model picker next to an input box: each method
works in its own copy of the workspace with its own memory, side by side, and the switcher picks the one you watch.

Saved projects are listed with their tasks; a task's row opens its sessions. **⋯** (or right-click) on a project:
**Add files…** (the next task sees them; not for example repos), **Download** (a zip of one method's workspace after its
last task), **Rename**, **Delete…** (asks again).

Each task runs through `demo/custom_runner.py`, the harness's own stack (RepoEnv in Apptainer, mini-swe-agent with the
pinned templates, the registered memory layers, a harness commit per task, the token ledger) minus grading: a free-form
task has no tests, so sandbox sessions are "not graded". Each memory method keeps a persistent workspace and memory store
per project, so task k+1 inherits the repository state and the memory of task k — the same sequential protocol as
ChainSWE. Files added between tasks are committed as `user: add files` before the next task, so they never count as the
agent's change. **↻** on a task row reruns that task (`fresh`): each chosen method's workspace goes back to just before
the task and its memory to the store as it was then — the newest snapshot `mem_store_<k>.json` of an earlier task, so a
task that was stopped (it left no snapshot and wrote no memory) is skipped over, and the store is empty when no earlier task
wrote memory — and that method's runs of later tasks are dropped. `/compare?cid=<project>` replays a project's methods side
by side. More images:
`apptainer pull --name <name>.sif docker://<image>` into `RSM_SIF_DIR` — an image whose name is not a chain's docker tag is
offered as a generic sandbox (the Docker image names `python:3.12` `sandbox-python-3.12.sif`).

### ChainSWE chains

- **The example chain** — a card for one recorded chain (by default `Python-Markdown_markdown_20191125`, "Table-of-contents fixes";
  *change* opens ⚙ at the chain picker) with a short description, the whole chain's tokens per method and its tasks
  (✓/✗, steps, cost) for the method in the switcher.
- **Live runs** — every saved live run, one entry per chain × memory method, newest first; selecting one opens it and
  keeps all the others listed in place. Runs that never finished a task are folded into one collapsed line. A run still
  going is listed first and streams into the transcript when opened.
- **▶ Run live** (in the playback bar) starts a new live run of the open chain with Knowledge Vault, Mem0 or no memory on
  the endpoint and model in ⚙ (about $1–5 of model credit; see *Live runs*).

### The transcript

The memory injected before the task (Knowledge Vault note lines with their DID / WHERE / VERIFY / CONTEXT / LESSON tags,
Mem0 facts, MemOp's guidance), the issue, then each agent step — thought, `$ command`, the output with its exit code —
the submitted patch, the check with the issue's tests, and what was written to memory for the next task. The pinned task
line above the transcript expands to the full issue.

**Playback bar**: ▶ Play replays the session at emulated real-time speed (each thought is typed out, the command appears,
a pause stands in for its execution, then the output; after the last step come the test check and the memory write;
with *continue to the next task* the chain plays through, so the note written after task k is seen injected into task
k+1); › reveals one step, ↺ restarts the task, ⇥ shows the whole task (on a complete task: the next one); space toggles
play. Pacing: the session's measured wall time (`secs` in `results.jsonl`) is spread over its steps in proportion to the
tokens each step generated, plus a short pause per command; the memory write lasts its measured `mem_write_secs`. The
ledger books actor calls at session end, so this emulates the recorded timing rather than replaying it.

**The box at the bottom**: in a sandbox it takes the next task (Run ▶ with Run with); on a ChainSWE session it asks about
the open session (`POST /api/ask`, context = the transcript), or chats with the model when ⚙ says so.

**Memory view** (`?view=memory`, or the compare-memory button in the Memory tab) replaces the transcript with Knowledge Vault and
Mem0 side by side for the whole chain or project: one row per task, *Retrieved + written* (what the task was shown before
it started, each item tagged with the task that wrote it, then what the writer stored after it, with its tokens, cost and
time) or *Saved store* (the whole store after that write, i.e. what the next task was shown). A row opens its task; Esc
goes back. Data: `GET /api/memory?run=&arm=&cid=`. Runs the demo launches save the store after every write
(`mem_store_<k>.json`, `RSM_MEM_SAVE=1`, `harness/mem_snapshot.py`) and are exact; recorded runs are read back from the
next task's injected block, so the last task's write shows only its item count.

**Live view** — a live run (chain or sandbox task) takes over the transcript: its status, the memory and the issue, then
each step as the harness produces it (the thought and `$ command` while the command executes, the output when it
returns), the test check and memory write, then the next task. **■ Stop** (playback bar while watching, or the banner of
a run you are not watching) asks for a second click, then ends the harness's whole process group (`POST
/api/live/{job}/stop`, or `POST /api/live/stop` with `{"jobs": [...]}`, empty = every running job; SIGKILL after 10 s);
finished tasks are kept and the run shows as stopped. The harness saves the trajectory after every model reply
(`RSM_LIVE_SAVE=1`, `harness/live_agent.py`, atomic writes); the page polls `GET /api/live/<job>/state` and
`GET /api/session`. For a sandbox rerun the state ignores the replaced attempt's results row and trajectory (files last
written before the job started) until the new attempt writes its own, so the view starts at "Starting" and never shows the
old session's steps or tokens.

### Right panel

**Changes** (the agent's final `git diff`, per file), **Files** (the session's whole repository as a collapsible tree —
the chain image at its base commit, or the sandbox workspace right after the task — with the files the agent read or
edited highlighted; a file opens in place; `GET /api/tree`, `GET /api/file`), **Memory** (before / after the task),
**Issue**, **Ledger** (every model call with prompt / cached / completion tokens and the priced USD). » collapses the panel.

### ⚙ (Agent)

Example chain (recorded run and chain), model endpoint (endpoint and model; the dot on ⚙ shows whether it answers), the
sandbox step limit, the Knowledge Vault version, the price row, playback (speed, continue to the next task, right panel
follows the agent), **Show details** (instance ids, harness instructions, model and exit status, raw counts, the harness
log of live runs) and chat with the model instead of asking about the session.

Links: `/?src=chains&cid=<chain>&kind=<kv|mem0|none>&order=<n>[&run=<live run id>][&view=memory]` opens a chain's task;
`/?cid=<project>&kind=&order=` a sandbox project's; a plain `/` the sandbox draft.

## Compare (`/compare`)

Columns are memory methods, fixed by position: **Knowledge Vault** (ours) on the left, **Mem0** · **MemOp** ·
**Full history** in the middle (turned on and off in ⚙ › Columns; Full history exists only in the recorded paper run, the
other methods also run live — `GET /api/meta` → `live_kinds`), **No memory** on the right. Each column header has only an **API** select (*Recorded (paper run)* or a model
endpoint) and a **Model** select, one task row (the task in progress and its step count; click it for the full issue) and
one numbers row (the whole chain's tokens, cost and resolved tasks). *steps* in that row opens every column's step chain:
one dot per agent step, one row per task (green = exit 0, red = non-zero exit or format error, blue = submit); a dot jumps
the replay to that step, the task label to the start of the task.

**Top bar**: Transcripts | Memory (the memory view of every column), ▶ Play all with › step, ↺ restart and ⇥ show all,
**Same model everywhere** (changing one column's API or model changes every column that can run it), **▶ Run live** /
**■ Stop**, the page links and ⚙. Run live runs every column that has a model endpoint chosen and no run of this chain yet
(`POST /api/configs/run`; runs are stored under run id `cfg__<provider>__<model>` with the memory method as the arm
directory, so each model's runs of a chain are found again later); a column without a run offers its own ▶. The footer is
one sentence — Knowledge Vault's tokens against the other columns — with *details* for the per-column totals.

**⚙**: Chain (the showcase chains as "Title — repo"; the list follows the columns: only chains where Knowledge Vault uses
fewer tokens than every enabled column — on pyccel MemOp uses fewer, so turning MemOp on moves to another chain and says
why), Columns, Knowledge Vault version, cost (per model used or one price row), playback (speed, play the whole chain),
the live-run step limit and Show details.

Links: `compare?cid=<chain>&cols=kv,mem0,none&api=<provider>&model=<model id>[&view=memory]` (`api` + `model` = the same
model in every column that can run live); `cid` may be a sandbox project, then the columns are its methods' runs;
`&run=v2main100` shows an older recorded run. Preset links from earlier versions of the page still open (translated once).

**A worked example: pyccel.** (The pages' default until MemOp joined the comparison: MemOp used fewer tokens on it, so it left
the example list; `?cid=pyccel_pyccel_20240307` still opens it.) On `pyccel_pyccel_20240307` (recorded paper run `v3main100`, Qwen3.8-27B actor) Knowledge
Vault used 0.59M tokens and resolved 2 of 3 tasks; Mem0 1.89M (1 of 3), No memory 2.51M (1 of 3) — $3.15 vs $9.77 vs
$12.95 at the opus5 row. Task 3 is the story: with 2.1K chars of notes injected, Knowledge Vault fixes it in 20 steps
($0.97); Mem0 injects nothing (its extractor stored no facts for the whole chain), repeats one `grep` 81 times until the
100-step limit ($7.24) and fails; no memory needs 37 steps ($1.70). Across v3main100, loops of ≥ 10 identical commands
occur in 12 Knowledge Vault, 20 Mem0 and 22 no-memory sessions (of 304 each), and Mem0 injected nothing in 104 of 204 later
tasks. Each step-chain row carries two honesty tags: **↻ loop ×N** when the agent repeated one command N ≥ 5 times (a
decoding loop of the actor, not a memory effect) and what was injected (**no mem** / **1.2K mem**).

**Memory-quality examples** (both methods injected memory; recorded run `v2main100`): `compare?cid=pyccel_pyccel_20240307&run=v2main100`
— task 3: both injected ≈2.3K chars; Knowledge Vault's note is concrete repository knowledge (files, line numbers, how
method dispatch works, verify commands) → ✓ 45 steps, Mem0's facts are statements about the assistant ("prefers to follow
existing patterns…") → ✗ 54 steps. `compare?cid=matthewwithanm_python-markdownify_20250302&run=v2main100` — task 3:
Knowledge Vault ✓ 20 steps with 2.1K chars (where `convert_br` / `_inline` live, which test files); Mem0 ✗ 33 steps with
19.8K chars of assistant-personality facts; no memory ✗ (no patch).

### Cost

**Cost is priced per call by the model that actually served it** (the router reports the routed model, e.g.
`gpt-5.2-2025-12-11`, in every response and the ledger stores it), using `demo/model_prices.json` (list prices; Anthropic
rows verified on platform.claude.com 2026-10-22, OpenAI rows from price aggregators because openai.com blocks automated
fetches — marked `verified: false`; the router's usage page is the authoritative bill). Recorded paper sessions carry no
model id, so they are priced at the price row in ⚙; "one price row for all" prices every column at the same rate, as the
paper's tables do.

## Report (`/report`)

The paper's results over the whole recorded run (`GET /api/summary`): the headline saving (Knowledge Vault | Mem0
toggle), two tiles (tokens and steps per task), cumulative cost over the chains, per-chain differences between the two
methods (a bar opens its chain), all methods side by side, and a chain explorer; "How the numbers are made" (collapsed,
at the bottom) explains grading, tokens, cost and estimates. The page starts nothing and never polls live runs.

- **Top bar** as on the other pages: the logo (→ `./`, Your sandbox → New project), Agent · Compare · Report and ⚙ with
  **Recorded run** (the paper runs only: "Paper run (v3, 100 chains)", "Paper run (v2)"), **Price row** and **Show details**
  (shared with the other pages).
- **Headline**: the saving, both methods' cost per chain and later-task resolve rate, and the price in plain words
  ("Priced at $5 per million input tokens and $25 per million output tokens").
- **Chain explorer** opens on the example chain (`showcase.default_cid`, then `meta.default_cid`); the picker has two
  groups, "Example chains — where Knowledge Vault saves tokens" ("Title — repo") and "The other N chains of the paper run"
  ("repo · YYYY-MM-DD"). Each task is headed by its summary (*Task summaries*); with Show details its instance id and the
  issue's own first line follow. Its bar shows agent tokens, then memory-writing tokens in a lighter shade of the method's
  colour (one key above the tasks). The memory given before a task and written after it are formatted as on the Agent page
  (DID / WHERE / VERIFY … rows, `static/player.js`), the issue as markdown. Each task has *Watch in the Agent view ▸*
  (`./?src=chains&cid=&kind=&order=`, paper run v3 only; with another run the links and the explorer's mention of them are
  gone), the chain one *Compare side by side ▸* (`compare?cid=`).
- Links: `report?cid=<chain>&kind=kv|mem0[&run=v2main100]`; an unknown chain or method falls back to the defaults and the
  URL is rewritten.
- If the server cannot be reached, a "Couldn't reach the demo server" card with *Try again* replaces the results.

## Task summaries

Every task has a `summary`: a short plain description (4-10 words, e.g. "Support set.clear() in translated Python code")
that the pages show instead of the issue's first line, which is often a tag, a heading or an issue number.
`demo/task_summaries.json` (`DEMO_TASK_SUMMARIES` overrides the path) holds one per ChainSWE task, shaped
`{"<cid>": {"<order>": "<summary>"}}`. The server loads it at start and re-reads it when its mtime changes (checked at
most once a second), so an edited file shows without a restart; a file that does not parse is ignored until it does.
A task without one gets the issue's first line, cleaned: leading `[tags]`, markdown `#`, `**`, backticks, "Fix #123:"-style
prefixes and trailing periods removed, cut at 80 chars on a word boundary with "…". A sandbox task's summary is the first
sentence of the user's own prompt, cleaned the same way. `summary` sits next to `title` (the raw first line, unchanged) on
every task-like object: `/api/chain/{cid}` (`chain.tasks` and each arm's sessions), `/api/session`, `/api/runchains`,
`/api/liveruns` and `/api/memory` sessions, `/api/showcase` (`chains[].tasks`) and `/api/custom/projects` tasks.

## Showcase chains

`demo/configs.json` → `showcase_chains` lists the chains the pages offer, each with a short `title` and a one-sentence
`blurb`: the chains of `showcase_run` (`v3main100`) where Knowledge Vault used at least `showcase_margin` (20 %) fewer
tokens than Mem0, MemOp and No memory alike and resolved at least as many tasks as each (8 chains). `GET /api/showcase` returns them with every
method's whole-chain tokens, resolved tasks, steps and USD (cached per price row, warmed at startup). The default chain is
`DEMO_DEFAULT_CID` when it names a listed chain, else `showcase_default`, else the first entry. The older keys (`configs`, `default_pair`, `showcase`) are kept for
`/api/configs` and old links.

## API

GET unless noted; `price=` (a row of `harness/prices.json`) and `kv=` (a Knowledge Vault version) are accepted where
numbers or Knowledge Vault runs are returned.

| endpoint | what |
|---|---|
| `/api/meta` | runs, arms, Knowledge Vault versions (`kv_versions`: id, label, note), price rows, chains, endpoints, `default_cid`, `live_enabled`, `live_kinds` (the methods live chain runs accept, in page order: `["kv", "mem0", "memop", "none"]`) |
| `/api/showcase` | the showcase chains with per-method stats and their `tasks` (order, title, summary), `kinds` (display names) and `kind_notes` (one-line descriptions) |
| `/api/runchains?run=&kinds=&cid=` | every chain of a run with per-method sessions (each with `summary`) and `started` per arm (unix time: the earliest mtime of the arm directory's files); `kinds` = comma list of `kv,mem0,memop,rawcat,none` (default `kv,mem0,none`; unknown → 400), `cid` = only that chain |
| `/api/summary?run=` · `/api/chain/{cid}?run=` | whole-run totals · one chain with all its arms (tasks and sessions carry `summary`) |
| `/api/session?run=&arm=&cid=&order=` | one session as a chat: turns (thought, command, output), memory, issue (`title`, `summary`, `problem_statement`), patch, grading, tokens; a turn's `thought` never repeats its command (`""` when the reply was only a command) |
| `/api/memory?run=&arm=&cid=` | what a memory method retrieved, wrote and kept per task |
| `/api/tree?cid=&run=&arm=&order=` · `/api/file?cid=&path=&arm=&order=` | the session's repository (chain image at its base commit, or the sandbox workspace after task `order`) · one file of it |
| `/api/providers` · `/api/endpoint?api_base=` | endpoint status and model lists · one OpenAI-compatible base checked |
| `POST /api/ask` · `POST /api/chat` | a question about a session · free chat, on the selected endpoint |
| `POST /api/live/start` `{cid, kind, provider, model, step_limit, memory_system}` | a new live run of a chain (fresh `live_<timestamp>` run id); `kind` = one of `live_kinds` (`memop` runs into arm `mem_memop`; `rawcat` → 400, recorded only); **409** `"A live run of this chain with <Method> (<model>) is already running — watch it or stop it first"` while one of the same chain, method and model (or the endpoint's default model) is going |
| `/api/live` · `/api/live/{job}` · `/api/live/{job}/state` · `/api/liveruns` | jobs of this server process · one job with its log tail · where it is now (streaming view) · every saved live run on disk |
| `POST /api/live/{job}/stop` · `POST /api/live/stop {jobs}` | stop one or several runs (process group; finished tasks are kept) |
| `/api/configs` · `POST /api/configs/run {cid, configs}` | presets of `configs.json` · start live runs for several configurations (inline `{kind, provider, model}` allowed, `kind` one of `live_kinds`); refused ones, such as a duplicate or a recorded-only method, are listed in `errors` and the rest start |
| `/api/sandboxes` | images under `RSM_SIF_DIR`: chain images (`kind: repo`) and generic ones (`default: true` on the one new projects use) |
| `/api/custom/projects` | sandbox projects with `label` (display-ready, e.g. "Empty folder · Python 3.12", "Example repo · owner/repo"), `source`, tasks, per-method runs and `downloads` |
| `POST /api/custom/project {name?, source?, git_url?, git_ref?, hint?, sandbox?}` | a new project: `source` = `empty` · `upload` · `git` · `image` (`sandbox` = a chain image); no `name` → named after the repo or the first words of `hint`, made unique; an existing name → 409; a failed clone → 400 "git clone failed: …" |
| `POST /api/custom/project/{name}/files?path=<relative path>[&unzip=1]` | one file as the raw request body (`application/octet-stream`); before the first task into the project's seed, later into every method's workspace too; `unzip=1` unpacks a `.zip`; limits `DEMO_UPLOAD_MAX_MB` per file, `DEMO_SEED_MAX_MB` in all, 5000 files → 413; 409 while a task runs |
| `/api/custom/project/{name}/download?kind=kv\|mem0\|none` | that method's workspace at its last commit, as a zip; 404 when it never ran there |
| `POST /api/custom/project/rename {name, new_name}` · `DELETE /api/custom/project/{name}` | rename · delete a project with its runs and workspaces |
| `POST /api/custom/start {project, task, kinds, provider, model, step_limit, order?, fresh?}` | run a task in a project with the listed methods (one job each; `kinds` from `kv, mem0, none, memop` — MemOp works through the API, the Agent page does not offer it yet); 409 "The <Method> run of this project is still going — wait for it or stop it" |

## Model endpoints (`demo/providers.py`)

| provider | endpoint | key |
|---|---|---|
| `local` | vLLM from `demo/slurm/serve_actor.sbatch` (or `RSM_API_BASE`) | none |
| `goodvision-test` | `https://api-test.goodvision.tech/v1` | `GOODVISION_API_KEY` |
| `goodvision-prod` | `https://api.goodvision.tech/v1` | `GOODVISION_API_KEY` |
| `azure` | `AZURE_OPENAI_ENDPOINT` | `AZURE_OPENAI_API_KEY` |

Put the keys in `demo/.env` (`cp demo/.env.example demo/.env`, git-ignored). `GET /api/providers` reports each endpoint's
live status with the router's own error code (`verified_identity_required` until a key is set, `invalid_api_key` for a
bad one) and lists the model market once the key works; the pages disable live runs until then. What is known about the
GoodVision API is in `demo/goodvision.md`. The harness changes it relies on are env-gated (`RSM_API_KEY`,
`RSM_OPENAI_STRICT`, `RSM_WRITER_API_KEY`, `RSM_FACTS_API_KEY`), so local runs are unchanged.

## Live runs

A live run launches `harness/mini_swe_agent.py` for one chain with `RSM_LEDGER=1` under the v3.0 protocol env
(TEAM_GUIDE §1) against an OpenAI-compatible endpoint; the mem0 extraction and the Knowledge Vault writer use the same
endpoint, so no other API is involved. Each run gets a fresh run id `live_<timestamp>` (compare: `cfg__<provider>__<model>`)
under `RSM_RUNS_DIR`, so nothing earlier is overwritten. Demo runs set `RSM_VALID_V2=1` (the writer validator's v2 rule
from `memory/ours/fusednote.py`), so a note that merely mentions the tool name is not discarded. While a run of a chain
with one method and model is going, a second identical one is refused (409 above): each costs about $1–5.

The memory methods a live run accepts are `live_kinds`: Knowledge Vault (`--memory-system <version>`, default
`fusednote`), Mem0 (`factsall`), **MemOp** (`--memory-system memop`, arm `mem_memop`: after each task one writer call
rewrites the guidance document with the official MemOp prompt, `memory/baselines/memop_prompt.py`) and No memory
(`--arm seq`). MemOp's writer, like Knowledge Vault's, uses the run's endpoint and model; through an OpenAI-style gateway
(`RSM_OPENAI_STRICT=1`, set for GoodVision and Azure) it sends `max_completion_tokens` with no `temperature` and no
`chat_template_kwargs`, and the key as `Authorization: Bearer` (`RSM_WRITER_API_KEY`, else `RSM_API_KEY`); the local vLLM
request is byte-identical to the recorded paper runs. Its guidance is the memory store's one item (`["guidance", text]`),
so the per-task snapshots (`mem_store_<k>.json`) and the memory view are exact. Its ledger rows (`note_writer`) price its
sessions like the other methods'.

Prerequisites:

- An Apptainer image per chain: `apptainer pull --name <tag>.sif docker://<docker_image>` into
  `RSM_SIF_DIR` (`tag` = the part after `:` in `docker_image` of `data/chainswe_data.jsonl`).
- A model endpoint: a vLLM server with the actor model (e.g. `vllm serve Qwen/Qwen3.8-27B-FP8 --served-model-name coder-next`),
  or a GoodVision / Azure key.
- `apptainer` on the host that runs the demo server (the harness execs the image for every step).

## Token sources (shown per session as `tok.src` / `writer_src`)

- `ledger` — `calls.jsonl` written by `harness/ledger.py` (live runs, MemOp included: its writer is a `note_writer` row): exact, every role.
- `traj` — actor usage from `traj_<k>_*.json` (recorded v2/v3 runs, which predate the ledger).
  The writer side is then **estimated** (`writer_src = estimate`) from the material the harness
  handed the writer, with the ledger's own chars→tokens rule: one fused-note distill call for Knowledge Vault, the two
  official extraction branches over `material[:23000]` for Mem0, one guidance rewrite per task for MemOp (Full history
  has no writer). The UI marks these.
- The vLLM used for the recorded runs did not report prefix-cache hits, so cache-aware = raw there.

## Tests

`tests/test_demo.py` (offline backend tests: `pytest -q tests/test_demo.py`) and `tests/ui/` (the three pages in headless
Chromium against a replay of the recorded run and a canned model, incl. sandbox tasks through the real harness; see
`tests/ui/README.md`).
