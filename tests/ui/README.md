# Browser tests for the demo UI (Playwright)

These drive the real pages (`/`, `/compare`, `/report`) in headless Chromium against a demo server whose *harness* is
replaced, so nothing calls a paid model:

- `replay_server.py` — the demo server (`demo/server.py` of this checkout) with `_start_live` swapped for a replayer: a
  live run of a ChainSWE chain copies a recorded `v3main100` session into a fresh `live_<timestamp>` run dir step by step
  (assistant reply first, then its observation), grades it after a pause, then moves to the next task. Every kind in
  `/api/meta` `live_kinds` replays its recorded arm: `kv` → `mem_fusednote`, `mem0` → `mem_factsall`, `memop` →
  `mem_memop`, `none` → `seq`; any other kind is refused with the real server's 400. The route handlers
  stay real, so their checks apply (for example a second live run of the same chain, method and model is refused with 409:
  suites that start live runs give them their own model names). Sandbox tasks still go through the real
  `demo/custom_runner.py` + Apptainer, against the canned model.
- `canned_model.py` — an OpenAI-compatible endpoint on 127.0.0.1:8858 (`CANNED_PORT` overrides the port): a scripted
  3-step agent (`ls`, write `NOTES.md`, submit), a one-line memory note for the Knowledge Vault / Mem0 writer prompts and
  a short `<snippet>…</snippet>` guidance for MemOp's writer, so a real MemOp run (harness or sandbox task) gets its
  guidance into task 2. No model, no network.

| suite | covers | server |
|---|---|---|
| `ui_index.js` | Agent page: the sandbox-first layout and the New project draft, ChainSWE mode (example chain card, live runs that stay listed when one is selected), the method switcher (same place and size in every state, dimmed methods with their reason), ⚙ pickers, playback, the right panel and the Files tree, the chat box, a replayed live run with Stop, phone width, dark scheme | replay, or read-only on a real server |
| `ui_sandbox.js` | Your sandbox end to end: Empty folder task with Knowledge Vault + Mem0, a second task that gets the memory, Your files (files, a folder, a single .zip), Git repo (clones `https://github.com/octocat/Hello-World`) and a failing git URL, Add files… then a task that sees them, Download, rename, delete; the switcher in step with Run with | replay only |
| `ui_compare.js` | Compare page (reads the default chain and every figure from the server, so it passes with any default): columns by position, API + Model per column, Same model everywhere, ⚙ What to compare (Example chains, Saved live runs, Sandbox projects: list format, URL, reload, back to Example chains) and Columns, task summaries, the step chain, the footer, links, phone width, dark scheme; replayed live runs per column, MemOp included | replay, or read-only (its live runs only on port 8799 or with `ALLOW_LIVE=1`) |
| `ui_memory.js` | the memory view (what each method retrieved, wrote and kept per task) on the Agent and Compare pages, also for a saved live run; task summaries as row names | any (starts nothing) |
| `ui_stop.js` | stopping live runs: the Compare bar's ■ Stop and a column's own ■ (every live method, MemOp included), the Agent page's banner and playback-bar stop | replay only (refuses to run on 8765 / 8766) |
| `ui_report.js` | Report page: top bar and ⚙ (paper runs only, Esc), plain words, each number once above the table, the methods table at 1280 / 1500 / 2000 px, the brand as on Compare, the headline's price in plain words, the chain explorer (opens on the default example chain from /api/showcase, Python-Markdown's "Table-of-contents fixes"; example chains first in their own group, task summaries as headings, the raw first line and instance ids only with Show details, memory notes as DID / WHERE / VERIFY rows and the issue as markdown, one key for the task bars, Agent / Compare links, `?cid=` / `?kind=` / `?run=` through a reload, no Watch promise for a run the Agent page does not play, unknown values fall back), no `/api/live` request, the failure card, phone width, dark scheme (the Agent page's background, a visible memory-writing bar segment) | any (starts nothing; aborts every non-GET `/api` request itself) |

Each script prints PASS / FAIL lines and a SUMMARY, lists page errors and failed requests, exits non-zero on a failure and
drops screenshots in `tests/ui/shots/` (`SHOTS=/abs/dir` moves them).

## Setup (once)

Keep everything off the OS disk if that matters on your machine:

```bash
mkdir -p ../tmp/pwtest && cd ../tmp/pwtest && npm init -y >/dev/null && npm install playwright@1.49.1
PLAYWRIGHT_BROWSERS_PATH=$PWD/browsers npx playwright install chromium      # needs libasound2 on the host
cd -
```

`ui_sandbox.js` (and the sandbox steps of the other suites) also need Apptainer, the generic `3.12` image in
`RSM_SIF_DIR` (`3.12.sif`, Python 3.12 with git) and network access to github.com.

## Servers

```bash
# from the repository root: the canned model (port 8858), then the replay server (port 8799)
python3 tests/ui/canned_model.py &
RSM_API_BASE=http://127.0.0.1:8858/v1 DEMO_PROVIDER=local DEMO_SERVED_NAME=coder-next \
  KV_UI_RUNS=/abs/scratch/ui_runs KV_UI_SRC=$PWD/runs/v3main100 RSM_SIF_DIR=/abs/sifs \
  FAKE_STEP_SECS=0.5 FAKE_TASKS=3 python3 tests/ui/replay_server.py
```

| env (replay server) | default | meaning |
|---|---|---|
| `KV_UI_RUNS` | `../tmp/ui_runs` | scratch run dir: live runs, sandbox projects (`custom/`), workspaces (`_custom_work/`), job logs (`_live/`); the recorded run is symlinked in |
| `KV_UI_SRC` | `runs/v3main100` | the recorded run the fake live runs replay |
| `FAKE_PORT` | `8799` | port |
| `FAKE_STEP_SECS` | `0.8` | seconds per replayed step |
| `FAKE_TASKS` | `2` | tasks a fake live run replays before it ends |

On the demo VM, `/srv/peter/kv-demo/tmp/devtest/servers.sh start|restart|status` starts both for the dev copy with
scratch data under `/srv/peter/kv-demo/tmp/devtest` (restart after editing `demo/*.py`; log `replay.log` there).

## Run

```bash
cd ../tmp/pwtest && export NODE_PATH=$PWD/node_modules PLAYWRIGHT_BROWSERS_PATH=$PWD/browsers
T=<this checkout>/tests/ui
for t in ui_index ui_compare ui_report ui_memory ui_stop ui_sandbox; do BASE=http://127.0.0.1:8799 node $T/$t.js; done
```

| env (suites) | meaning |
|---|---|
| `BASE` | the server (default `http://127.0.0.1:8799`) |
| `SHOTS` | screenshot directory |
| `PREFIX` | starts the name of every project `ui_index.js` / `ui_sandbox.js` create, and of the model names the live-run suites use (`<PREFIX>live`, `<PREFIX>stop`; default `ui-`), so several people can test on one server |
| `RO=1` | `ui_index.js`, `ui_compare.js` and `ui_memory.js` read-only, for a server with real model keys: every non-GET `/api` request the page makes is aborted and the steps that call a model or start a run are skipped (implied when `BASE` is on port 8766 or 8765) |
| `ALLOW_LIVE=1` | `ui_compare.js`: run its replayed live runs on a replay server that is not on port 8799 |

**Read-only on a real server** (e.g. the dev server on 127.0.0.1:8766, which has real keys and real data): only
`ui_index.js` (read-only by itself on 8766 / 8765, `RO=1` elsewhere), `ui_compare.js` (no live runs off port 8799),
`ui_memory.js` and `ui_report.js`. `ui_sandbox.js` refuses to run there; `ui_stop.js` starts runs, so it belongs on the
replay server only. A script of your own against such a server should install the same guard before anything else:
`await page.route('**/api/**', r => r.request().method() === 'GET' ? r.continue() : r.abort())`.

Known expectation: on a server started before task summaries existed, `ui_report.js` passes with the issue's first lines
as headings and says "THIS SERVER SENDS NO SUMMARIES (restart it)" in the summary check.

Known expectation: "step reveals one more turn" fails when playback was paused mid-step — Step completes the current step
first; that is the intended behaviour.

Top-bar measurements: `ui_index.js` checks that the method switcher's box is identical across states in a browser with
classic scrollbars (`chromium.launch({ ignoreDefaultArgs: ['--hide-scrollbars'] })`) at 2000, 1500, 1280 and 420 px, and
with phone emulation (`isMobile`, `hasTouch`) at 420 px; `ui_sandbox.js` runs entirely with classic scrollbars. At 860 px
and narrower the Agent page reserves the scrollbar gutter, so a 420 px screenshot taken in Playwright's default
(hidden-scrollbar) mode shows the three-row top bar of a narrowed desktop window, while a phone shows two rows.
