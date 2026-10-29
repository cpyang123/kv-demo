# Browser tests for the demo UI (Playwright)

These drive the real pages in headless Chromium against a demo server whose *harness* is replaced:

- `replay_server.py` — the demo server (`demo/server.py`) with `_start_live` swapped for a replayer that feeds a
  recorded `runs/v3main100` session into a fresh `live_<timestamp>` run dir step by step (assistant reply first,
  then its observation), so "Run this chain live" streams without a model or a chain image. Sandbox tasks still
  go through the real `custom_runner.py` + Apptainer; point `RSM_API_BASE` at `canned_model.py` for those.
- `canned_model.py` — an OpenAI-compatible endpoint on :8858 with a scripted 3-step agent (`ls`, write `NOTES.md`,
  submit) and a one-line Knowledge Vault note for the writer prompt.
- `ui_index.js` (agent view: playback, panes, composer, sandbox tab, filters, selectors, phone width, dark scheme),
  `ui_compare.js`, `ui_report.js`, `ui_sandbox.js` (end to end: create a project, run a task with KV + none through
  the real harness in the `3.12` image, watch it live, run a second task that gets the note injected).

Setup (once; keep everything off the OS disk if that matters on your machine):

```bash
mkdir -p ../tmp/pwtest && cd ../tmp/pwtest && npm init -y >/dev/null && npm install playwright@1.49.1
PLAYWRIGHT_BROWSERS_PATH=$PWD/browsers npx playwright install chromium      # needs libasound2 on the host
cd -
```

Run:

```bash
# terminal 1: canned model + replay server (port 8799; FAKE_STEP_SECS paces the replay, FAKE_TASKS caps the chain)
python3 tests/ui/canned_model.py &
RSM_API_BASE=http://127.0.0.1:8858/v1 FAKE_STEP_SECS=0.5 FAKE_TASKS=3 python3 tests/ui/replay_server.py
# terminal 2
cd ../tmp/pwtest && export NODE_PATH=$PWD/node_modules PLAYWRIGHT_BROWSERS_PATH=$PWD/browsers
for t in ui_index ui_compare ui_report ui_sandbox; do node ../../repo/tests/ui/$t.js; done   # BASE=http://... overrides the server
```

Each script prints PASS/FAIL lines and a SUMMARY, lists page errors and failed requests, and drops screenshots in
`tests/ui/shots/` (`SHOTS=` to move them). `ui_sandbox.js` needs Apptainer and the `3.12` sandbox image in `RSM_SIF_DIR`.
Known expectation: "step reveals one more turn" fails when playback was paused mid-step — Step completes the current
step first; that is the intended behaviour.
