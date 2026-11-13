"""Unit tests for the demo backend (demo/server.py, demo/providers.py, demo/deploy/check_goodvision.py).

Offline: the network is monkeypatched; the recorded paper run runs/v3main100 (tracked in git) is the fixture.
Run:  pytest -q tests/test_demo.py
"""
from __future__ import annotations

import collections
import io
import json
import os
import pathlib
import stat
import subprocess
import sys
import threading
import time
import types
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ.setdefault("RSM_SIF_DIR", str(ROOT / "tests" / "_no_sifs"))     # no images in CI: live runs must be refused cleanly
os.environ.setdefault("RSM_HF_HOME", "/tmp/kv_hf_home")
os.environ.setdefault("DEMO_LIVE_DIR", "/tmp/kv_demo_live")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "demo"))

import providers as PV                          # noqa: E402
import server as S                              # noqa: E402
sys.path.insert(0, str(ROOT / "demo" / "deploy"))
import check_goodvision as CG                   # noqa: E402
from fastapi.testclient import TestClient       # noqa: E402

client = TestClient(S.app)
SHOWCASE = "pyccel_pyccel_20240307"            # a recorded chain with all five methods (data tests use it; it left the showcase list)
DEFAULT_CID = "Python-Markdown_markdown_20191125"   # the chain the pages open on (configs.json showcase_default)


# ------------------------------------------------------------------ pure helpers

def test_price_for_model_prefix_matching():
    table = {"gpt-5.5": {"input": 5, "cached_input": 0.5, "output": 30}, "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.5}, "claude-sonnet-5": {"input": 2, "cached_input": 0.2, "output": 10}}
    assert S._price_for_model("gpt-5.5-2026-04-24", table)[1] == "gpt-5.5"
    assert S._price_for_model("gpt-5.4-mini-2026-03-17", table)[1] == "gpt-5.4-mini"
    assert S._price_for_model("claude-sonnet-5", table)[1] == "claude-sonnet-5"
    assert S._price_for_model("coder-next", table) == (None, None)
    assert S._price_for_model(None, table) == (None, None)


def test_usd_by_model_prices_each_call_by_its_model():
    table = {"gpt-5.5": {"input": 5.0, "cached_input": 0.5, "output": 30.0}}
    rows = [{"model": "gpt-5.5-2026-04-24", "prompt_tokens": 1_000_000, "cached_tokens": 0, "completion_tokens": 100_000, "role": "actor"},
            {"model": "gpt-5.5", "prompt_tokens": 1_000_000, "cached_tokens": 1_000_000, "completion_tokens": 0, "role": "note_writer"}]
    usd, by_model, unknown = S._usd_by_model(rows, table)
    assert usd == pytest.approx(5.0 + 3.0 + 0.5)
    assert sum(by_model.values()) == 2 and unknown == []
    usd2, _, unknown2 = S._usd_by_model([{"model": None, "prompt_tokens": 10, "cached_tokens": 0, "completion_tokens": 0}], table)
    assert unknown2 == ["model not recorded"] and usd2 >= 0


def test_parse_turns_and_diff():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "<pr_description>\nConsider the following PR description:\nFix it\n</pr_description>\n<instructions>do</instructions>"},
            {"role": "assistant", "content": "THOUGHT: look\n\n```mswea_bash_command\nls -la\n```", "extra": {"response": {"model": "gpt-5.5-2026-04-24", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}}},
            {"role": "user", "content": "<returncode>0</returncode>\n<output>\nREADME.md\n</output>"},
            {"role": "assistant", "content": "THOUGHT: oops two blocks\n```mswea_bash_command\na\n```\n```mswea_bash_command\nb\n```"},
            {"role": "user", "content": "Format error:\n<error>Expected exactly 1 action, found 2.</error>"},
            {"role": "assistant", "content": "THOUGHT: submit\n```mswea_bash_command\necho COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git diff\n```"},
            {"role": "exit", "content": "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n-a\n+b\n+c\n", "extra": {"exit_status": "Submitted"}}]
    turns = S._parse_turns(msgs)
    assert [t["step"] for t in turns] == [1, 2, 3]
    assert turns[0]["command"] == "ls -la" and turns[0]["observation"]["returncode"] == 0 and turns[0]["model"] == "gpt-5.5-2026-04-24"
    assert turns[1]["observation"]["output"].startswith("<error>") or "Format error" in msgs[5]["content"]
    assert turns[2]["exit"] == "Submitted"
    mem, pr, ins = S._split_first_user(msgs[1]["content"])
    assert mem == "" and pr == "Fix it" and ins == "do"
    files = S._parse_diff(msgs[7]["content"])
    assert files == [{"path": "x.py", "added": 2, "removed": 1, "diff": files[0]["diff"]}]


def test_thought_never_repeats_the_command():
    """A reply without THOUGHT: shows its prose without the command block (and the fences / tool-call tags some models
    leave around it), '' when it was only a command; replies with THOUGHT: parse as before; the command is the one
    mini-swe-agent ran (its action regex: the closing fence starts a line)."""
    def one(content):
        return S._parse_turns([{"role": "assistant", "content": content, "extra": {"response": {"usage": {"prompt_tokens": 7, "completion_tokens": 3}}}}])[0]
    t = one("```mswea_bash_command\nls -la /x\n```")
    assert t["thought"] == "" and t["command"] == "ls -la /x" and t["raw"] == "```mswea_bash_command\nls -la /x\n```" and t["usage"]["prompt"] == 7
    t = one("File created. Now the patch.\n\n```mswea_bash_command\ncd /x && git diff\n```")
    assert t["thought"] == "File created. Now the patch." and t["command"] == "cd /x && git diff"
    t = one("THOUGHT: look first\n\n```mswea_bash_command\nls\n```")
    assert t["thought"] == "look first" and t["command"] == "ls"
    assert one("THOUGHT: two\nlines\n```mswea_bash_command\nls\n```\ntrailing")["thought"] == "two\nlines"
    for debris in ("```mswea_bash_command\nls\n```\n</parameter>", "THOUGHT\n\n</think>\n\nTHOUGHT\n\n```mswea_bash_command\nls\n```",
                   "```mswea_bash_command\nls\n```\n</function>\n</tool_call>", "THOUGHT: ```mswea_bash_command\nls\n```"):
        t = one(debris)
        assert t["thought"] == "" and t["command"] == "ls", debris
    assert one("Let me look.\n```mswea_bash_command\nls\n```\n</function>\n</tool_call>")["thought"] == "Let me look."
    t = one("Let me look\n```mswea_bash_command\nls -la")                       # unclosed: nothing ran, the text stays readable
    assert t["command"] == "" and t["thought"] == "Let me look\n\nls -la"
    heredoc = "```mswea_bash_command\ncat > A.md << 'EOF'\n# A\n   ```bash\n   pip install a\n   ```\nEOF\ncat A.md\n```"
    t = one(heredoc)                                                              # ``` inside the command (a markdown heredoc)
    assert t["command"].endswith("EOF\ncat A.md") and "pip install a" in t["command"] and t["thought"] == ""
    # a recorded session: replies with THOUGHT: are unchanged, and no thought holds a command block
    import glob, re
    for tf in sorted(glob.glob(str(S.RUNS / "v3main100" / f"{SHOWCASE}__s0__mem_fusednote" / "traj_*.json"))):
        msgs = json.load(open(tf))["messages"]
        turns = S._parse_turns(msgs)
        replies = [m["content"] or "" for m in msgs if m.get("role") == "assistant"]
        assert len(turns) == len(replies) and not any("```" in t["thought"] for t in turns)
        for t, c in zip(turns, replies):
            th = re.search(r"THOUGHT:\s*([\s\S]*?)(?:\n```|$)", c)
            if th and "```" not in th.group(1):
                assert t["thought"] == th.group(1).strip()


# ------------------------------------------------------------------ API over the recorded run

def test_meta_lists_recorded_runs_and_flags():
    m = client.get("/api/meta").json()
    assert "v3main100" in m["runs"] and m["arms"] == {"none": "seq", "mem0": "mem_factsall", "kv": "mem_fusednote"}
    assert "live_enabled" in m and any(p["name"] == "goodvision-test" for p in m["providers"])


def test_runchains_has_three_arms_for_showcase_chain():
    rc = client.get("/api/runchains", params={"run": "v3main100"}).json()
    row = next(c for c in rc["chains"] if c["cid"] == SHOWCASE)
    assert set(row["arms"]) == {"kv", "mem0", "none"}
    kv, m0 = row["arms"]["kv"], row["arms"]["mem0"]
    assert kv["resolved"] == 2 and m0["resolved"] == 1 and kv["usd"] < m0["usd"]
    assert len(kv["sessions"]) == 3 and all("usd_model" in s for s in kv["sessions"])


def test_session_endpoint_showcase_task3():
    s = client.get("/api/session", params={"run": "v3main100", "arm": "mem_fusednote", "cid": SHOWCASE, "order": 3}).json()
    assert s["exit_status"] == "Submitted" and s["result"]["resolved"] is True
    assert len(s["turns"]) == 20 and s["memory"] and "list_methods.py" in s["memory"]
    assert {f["path"] for f in s["changes"]} >= {"pyccel/ast/builtin_methods/list_methods.py"}
    assert s["tok"]["src"] == "traj" and s["tok"]["writer_src"] == "estimate"
    m0 = client.get("/api/session", params={"run": "v3main100", "arm": "mem_factsall", "cid": SHOWCASE, "order": 3}).json()
    assert m0["memory"] == "" and m0["result"]["resolved"] is False and len(m0["turns"]) == 100


def test_summary_and_configs():
    sm = client.get("/api/summary", params={"run": "v3main100"}).json()
    assert sm["common_chains"] == 100 and sm["arms"]["kv"]["usd_chain"] < sm["arms"]["mem0"]["usd_chain"]
    cfg = client.get("/api/configs").json()
    ids = {c["id"] for c in cfg["configs"]}
    assert {"kv_router", "mem0_gpt55", "kv_v3", "mem0_v3"} <= ids
    rec = next(c for c in cfg["configs"] if c["id"] == "kv_v3")
    assert rec["run_id"] == "v3main100" and SHOWCASE in rec["chains"]
    assert cfg["showcase"]["cid"] == SHOWCASE


def test_missing_run_and_unknown_chain_are_404():
    assert client.get("/api/summary", params={"run": "nope"}).status_code == 404
    assert client.get("/api/session", params={"run": "v3main100", "arm": "seq", "cid": "nope", "order": 1}).status_code == 404


def test_live_start_refused_without_image_or_when_disabled(monkeypatch):
    r = client.post("/api/live/start", json={"cid": SHOWCASE, "kind": "kv", "provider": "goodvision-test", "model": "auto"})
    assert r.status_code == 400 and "SIF missing" in r.json()["detail"]
    monkeypatch.setenv("DEMO_DISABLE_LIVE", "1")
    r = client.post("/api/live/start", json={"cid": SHOWCASE, "kind": "kv"})
    assert r.status_code == 403
    r = client.post("/api/custom/start", json={"project": "x", "task": "t", "kinds": ["kv"]})
    assert r.status_code == 403
    assert client.get("/api/meta").json()["live_enabled"] is False


def test_recorded_config_is_not_rerunnable():
    r = client.post("/api/configs/run", json={"cid": SHOWCASE, "configs": ["kv_v3"]}).json()
    assert r["jobs"] == [] and r["errors"] and "recorded" in r["errors"][0]


def test_a_second_live_run_of_the_same_chain_method_and_model_is_refused(monkeypatch):
    """Each live run costs $1-5: while one of a chain x memory system x model is going, the route handlers refuse another
    (409; /api/configs/run lists it in errors and starts the rest). Another model or method, a stopped or stopping run
    and a sandbox task never block."""
    started = []

    def fake_start(req):                                   # what replay_server.py does: register a running job, no harness
        job = f"dupjob{len(started)}"
        S.JOBS[job] = {"id": job, "cid": req.cid, "kind": req.kind, "arm": S.ARMS[req.kind], "run_id": req.run_id or "live_x",
                       "started": time.time(), "status": "running", "provider": req.provider, "model": S._live_model(req), "pid": 0}
        started.append(job)
        return S.JOBS[job]
    monkeypatch.setattr(S, "_start_live", fake_start)
    try:
        body = {"cid": SHOWCASE, "kind": "kv", "provider": "local", "model": "m-dup"}
        assert client.post("/api/live/start", json=body).status_code == 200
        r = client.post("/api/live/start", json=body)
        assert r.status_code == 409 and r.json()["detail"] == "A live run of this chain with Knowledge Vault (m-dup) is already running — watch it or stop it first"
        assert len(started) == 1
        assert client.post("/api/live/start", json={**body, "model": "m-other"}).status_code == 200     # another model
        assert client.post("/api/live/start", json={**body, "kind": "mem0"}).status_code == 200         # another method
        assert client.post("/api/live/start", json={"cid": SHOWCASE, "kind": "none", "provider": "local"}).status_code == 200
        r = client.post("/api/live/start", json={"cid": SHOWCASE, "kind": "none", "provider": "local", "model": "coder-next"})
        assert r.status_code == 409 and "with No memory (coder-next)" in r.json()["detail"]            # no model = the endpoint's default
        r = client.post("/api/configs/run", json={"cid": SHOWCASE, "configs": [{"kind": "kv", "provider": "local", "model": "m-dup"},
                                                                              {"kind": "kv", "provider": "local", "model": "m-third"}]}).json()
        assert r["errors"] == ["A live run of this chain with Knowledge Vault (m-dup) is already running — watch it or stop it first"]
        assert [j["model"] for j in r["jobs"]] == ["m-third"] and len(started) == 5
        S.JOBS[started[0]]["stopping"] = True                                                         # Stop pressed: no longer blocks
        assert client.post("/api/live/start", json=body).status_code == 200
        S.JOBS[started[-1]]["status"] = "stopped"
        assert client.post("/api/live/start", json=body).status_code == 200
        S.JOBS["dupcustom"] = {"id": "dupcustom", "cid": SHOWCASE, "kind": "mem0", "custom": True, "status": "running", "model": "m-sb"}
        assert client.post("/api/live/start", json={**body, "kind": "mem0", "model": "m-sb"}).status_code == 200
        assert S._live_duplicate(S.LiveReq(cid=SHOWCASE, kind="mem0", provider="nope")) is None       # no model id at all: nothing to compare
        # a double click: two requests at once start one run (the check and the start are one step)
        monkeypatch.setattr(S, "_start_live", lambda req: (time.sleep(0.3), fake_start(req))[1])
        codes = []
        ths = [threading.Thread(target=lambda: codes.append(client.post("/api/live/start", json={**body, "model": "m-twice"}).status_code)) for _ in range(2)]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        assert sorted(codes) == [200, 409]
    finally:
        for j in started + ["dupcustom"]:
            S.JOBS.pop(j, None)


# ------------------------------------------------------------------ providers (network mocked)

def _fake_http(models_status=200, models=("auto", "gpt-5.5", "claude-sonnet-5"), chat=None):
    chat = chat or {}
    def _get(url, key=None, timeout=8):
        if models_status != 200:
            return models_status, {"error": {"code": "invalid_api_key", "message": "API key is invalid, revoked or expired"}}, {}
        return 200, {"data": [{"id": m} for m in models]}, {}
    def _post(url, key, payload, timeout=30):
        m = payload.get("model"); st, body = chat.get(m, (200, {"model": m, "choices": [{"message": {"content": "OK"}}], "usage": {}}))
        return st, body, {}
    return _get, _post


def test_provider_check_ok_and_blocked_family(monkeypatch):
    monkeypatch.setenv("GOODVISION_API_KEY", "gv_test")
    blocked = {"claude-sonnet-5": (400, {"error": {"code": "aws_invoke_error", "message": "Access to Anthropic models is not allowed for this account."}})}
    g, p = _fake_http(chat=blocked)
    monkeypatch.setattr(PV, "_get", g); monkeypatch.setattr(PV, "_post_json", p)
    c = PV.check("goodvision-test")
    assert c["ok"] is True and c["models"] == ["auto", "claude-sonnet-5", "gpt-5.5"]
    assert c["families"]["anthropic"]["ok"] is False and c["families"]["openai"]["ok"] is True
    assert "upstream blocked for anthropic" in c["message"]


def test_provider_check_is_cached_and_avoids_blocked_default(monkeypatch):
    """Page loads must not re-probe the router (each strict probe sends paid completions); a blocked default model is swapped."""
    monkeypatch.setenv("GOODVISION_API_KEY", "gv_test")
    calls = []
    blocked = {"claude-sonnet-5": (400, {"error": {"code": "aws_invoke_error", "message": "blocked"}})}
    g, p = _fake_http(chat=blocked)
    monkeypatch.setattr(PV, "_get", lambda *a, **k: (calls.append(1), g(*a, **k))[1]); monkeypatch.setattr(PV, "_post_json", p)
    monkeypatch.setitem(PV.PROVIDERS["goodvision-test"], "default_model", "claude-sonnet-5")
    monkeypatch.setattr(S, "_PROV_CACHE", {})
    a = S._provider_check("goodvision-test"); b = S._provider_check("goodvision-test")
    assert len(calls) == 1 and a is b
    assert a["default_model"] == "gpt-5.5" and "is refused" in a["default_model_note"]
    S._provider_check("goodvision-test", fresh=True)
    assert len(calls) == 2


def test_provider_check_invalid_key_and_empty_market(monkeypatch):
    monkeypatch.setenv("GOODVISION_API_KEY", "gv_bad")
    g, p = _fake_http(models_status=401)
    monkeypatch.setattr(PV, "_get", g); monkeypatch.setattr(PV, "_post_json", p)
    c = PV.check("goodvision-test")
    assert c["ok"] is False and c["code"] == "invalid_api_key"
    g, p = _fake_http(models=(), chat={"auto": (422, {"error": {"code": "no_candidate", "message": "No model offer satisfies the effective policy"}})})
    monkeypatch.setattr(PV, "_get", g); monkeypatch.setattr(PV, "_post_json", p)
    c = PV.check("goodvision-test")
    assert c["ok"] is False and c["code"] == "no_candidate" and "model market is empty" in c["message"]


def test_provider_env_for_harness_strict_vs_local():
    e = PV.env_for_harness("goodvision-test", "gpt-5.5")
    assert e["RSM_OPENAI_STRICT"] == "1" and e["RSM_API_BASE"].endswith("/v1") and e["RSM_FACTS_MODEL"] == "gpt-5.5" and "RSM_ACTOR_NOTHINK" not in e
    e = PV.env_for_harness("local", "coder-next", api_base="http://127.0.0.1:8000/v1")
    assert e["RSM_ACTOR_NOTHINK"] == "1" and "RSM_OPENAI_STRICT" not in e


# ------------------------------------------------------------------ GoodVision connectivity checker

def test_check_goodvision_outcomes(monkeypatch):
    def fake_call(base, key, method, path, body=None, timeout=60):
        if path == "/v1/models":
            return (200, {"data": [{"id": "auto"}, {"id": "gpt-5.4-mini"}, {"id": "claude-sonnet-5"}]}, {}) if key == "good" else (401, {"error": {"code": "invalid_api_key", "message": "bad"}}, {})
        if body and body.get("tools"):
            return 422, {"error": {"code": "no_candidate", "message": "No model offer satisfies the effective policy"}}, {}
        if body["model"].startswith("claude"):
            return 400, {"error": {"code": "aws_invoke_error", "message": "Access to Anthropic models is not allowed"}}, {}
        return 200, {"model": "gpt-5.2-2025-12-11", "choices": [{"message": {"content": "OK"}}]}, {"x-gv-selected-model": "gpt-5.2", "x-gv-route-mode": "auto", "x-gv-decision-id": "dec_1"}
    monkeypatch.setattr(CG, "call", fake_call)
    r = CG.run_checks("https://api-test.goodvision.tech/v1", "good")
    assert r["ok"] and r["routing"]["selected"] == "gpt-5.2" and r["families"]["anthropic"]["ok"] is False and r["tools"]["ok"] is False
    assert any("anthropic" in d for d in r["degraded"]) and any("tool calls" in d for d in r["degraded"])
    r = CG.run_checks("https://api-test.goodvision.tech/v1", "bad")
    assert r["ok"] is False and "invalid_api_key" in r["error"]
    r = CG.run_checks("https://api-test.goodvision.tech/v1", "")
    assert r["ok"] is False and "not set" in r["error"]


# ------------------------------------------------------------------ secret scrubbing of run artifacts

def test_scrub_removes_keys_from_traj(tmp_path):
    import scrub
    key = "gv_0123456789abcdef_" + "A" * 43
    t = tmp_path / "traj_1_x.json"
    t.write_text(json.dumps({"info": {"config": {"model": {"model_kwargs": {"api_key": key, "max_tokens": 5}}}}, "messages": [{"role": "user", "content": f"Authorization: Bearer {key}"}]}))
    (tmp_path / "calls.jsonl").write_text(json.dumps({"role": "actor", "note": key}) + "\n")
    n = scrub.scrub_dir(tmp_path, [key])
    assert n >= 3
    d = json.loads(t.read_text())
    assert d["info"]["config"]["model"]["model_kwargs"]["api_key"] == "REDACTED" and d["info"]["config"]["model"]["model_kwargs"]["max_tokens"] == 5
    assert key not in t.read_text() and key not in (tmp_path / "calls.jsonl").read_text()
    assert scrub.scrub_obj({"api_key": "EMPTY"})[1] == 0          # local vLLM placeholder is left alone


# ------------------------------------------------------------------ live view: job state while a task is in progress

def test_live_state_and_session_follow_a_task_in_progress(monkeypatch, tmp_path):
    """The streaming transcript polls /api/live/{job}/state and /api/session for the task in progress: a trajectory that
    is still growing (last reply without its observation yet, no results row) must be served, not 404'd."""
    import glob, shutil
    arm = S.ARMS["kv"]
    src = pathlib.Path(S.RUNS) / "v3main100" / f"{SHOWCASE}__s0__{arm}"
    monkeypatch.setattr(S, "RUNS", tmp_path)
    out = tmp_path / "demo_live" / f"{SHOWCASE}__s0__{arm}"; out.mkdir(parents=True)
    log = tmp_path / "job.log"; log.write_text("$ harness\n=== chain\n")
    S.JOBS["fakejob1"] = {"id": "fakejob1", "cid": SHOWCASE, "kind": "kv", "arm": arm, "run_id": "demo_live", "started": 0.0, "status": "running",
                          "returncode": None, "log": str(log), "api_base": "x", "provider": "local", "model": "m", "pid": 0}
    try:
        st = client.get("/api/live/fakejob1/state").json()
        assert st["phase"] == "starting" and st["cur"] == 1 and st["done"] == [] and st["has_traj"] is False and st["n_tasks"] == 3
        # the harness saved the trajectory right after a model reply: 3 complete steps + one whose command is still running
        tf = sorted(glob.glob(f"{src}/traj_1_*.json"))[0]
        t = json.load(open(tf)); part = dict(t); part["messages"] = t["messages"][:2 + 2 * 3 + 1]; part["info"] = dict(t["info"], exit_status="", submission="")
        (out / os.path.basename(tf)).write_text(json.dumps(part))
        st = client.get("/api/live/fakejob1/state").json()
        assert st["phase"] == "agent" and st["has_traj"] is True and st["cur"] == 1
        sess = client.get(f"/api/session?run=demo_live&arm={arm}&cid={SHOWCASE}&order=1")
        assert sess.status_code == 200, sess.text
        sess = sess.json()
        assert len(sess["turns"]) == 4 and sess["turns"][3]["observation"] is None and sess["turns"][2]["observation"] is not None
        assert not sess["exit_status"] and sess["result"]["resolved"] is None and sess["problem_statement"]
        # task 1 graded, task 2 started
        shutil.copy(tf, out / os.path.basename(tf))
        (out / "results.jsonl").write_text(json.dumps({"order": 1, "instance": "i1", "resolved": True, "exit": "Submitted", "n_steps": 27}) + "\n")
        tf2 = sorted(glob.glob(f"{src}/traj_2_*.json"))[0]; shutil.copy(tf2, out / os.path.basename(tf2))
        st = client.get("/api/live/fakejob1/state").json()
        assert st["done"] == [1] and st["cur"] == 2 and st["phase"] == "agent" and st["results"][0]["resolved"] is True
        S.JOBS["fakejob1"]["status"] = "done"
        st = client.get("/api/live/fakejob1/state").json()
        assert st["phase"] == "finished" and "harness" in st["log_tail"]
        assert client.get("/api/live/nope/state").status_code == 404
        # a sandbox task is a single order: nothing before it counts as "done", nothing after it is expected
        S.JOBS["fakejob2"] = {**S.JOBS["fakejob1"], "id": "fakejob2", "cid": "myproj", "run_id": "custom", "custom": True, "order": 2, "status": "running"}
        st = client.get("/api/live/fakejob2/state").json()
        assert st["orders"] == [2] and st["n_tasks"] == 1 and st["cur"] == 2 and st["done"] == [] and st["phase"] == "starting"
        # ... also when the arm already has the results of its earlier tasks (task 2 of a sandbox project)
        pdir = tmp_path / "custom" / f"myproj__s0__{arm}"
        pdir.mkdir(parents=True)
        (pdir / "results.jsonl").write_text(json.dumps({"order": 1, "instance": "myproj-1-x", "exit": "Submitted"}) + "\n")
        st = client.get("/api/live/fakejob2/state").json()
        assert st["done"] == [] and st["results"] == [] and st["cur"] == 2 and st["phase"] == "starting"
        with open(pdir / "results.jsonl", "a") as f:
            f.write(json.dumps({"order": 2, "instance": "myproj-2-y", "exit": "Submitted"}) + "\n")
        st = client.get("/api/live/fakejob2/state").json()
        assert st["done"] == [2] and [r["order"] for r in st["results"]] == [2] and st["cur"] is None
    finally:
        S.JOBS.pop("fakejob1", None); S.JOBS.pop("fakejob2", None)


def test_live_state_of_a_rerun_ignores_the_replaced_attempt(monkeypatch, tmp_path):
    """↻ Rerun of sandbox task 3: until the new runner rewinds the arm, results.jsonl still has the old attempt's row 3 and
    traj_3_*.json is the old attempt's. Files written before the job started are not the new attempt: the state must say
    'starting' (done [], no trajectory), not 'Wrapping up' with the old session's steps and tokens."""
    arm = S.ARMS["kv"]
    monkeypatch.setattr(S, "RUNS", tmp_path)
    d = tmp_path / "custom" / f"rerun__s0__{arm}"; d.mkdir(parents=True)
    rows = lambda *os_: "".join(json.dumps({"order": o, "instance": f"rerun-{o}", "exit": "Submitted", "mem_nodes": 1}) + "\n" for o in os_)
    (d / "results.jsonl").write_text(rows(1, 3))
    traj = d / "traj_3_rerun-3.json"; traj.write_text(json.dumps({"messages": [], "info": {"exit_status": "Submitted"}}))
    (d / "calls.jsonl").write_text("{}\n")
    old = time.time() - 60
    for p in d.iterdir():
        os.utime(p, (old, old))
    log = tmp_path / "rerun.log"; log.write_text("$ custom_runner --fresh\n")
    S.JOBS["rerunjob"] = {"id": "rerunjob", "cid": "rerun", "kind": "kv", "arm": arm, "run_id": "custom", "order": 3, "custom": True,
                          "started": time.time() - 1, "status": "running", "returncode": None, "log": str(log), "model": "m", "pid": 0}
    try:
        st = client.get("/api/live/rerunjob/state").json()
        assert st["done"] == [] and st["results"] == [] and st["cur"] == 3 and st["has_traj"] is False and st["phase"] == "starting"
        assert [r["order"] for r in client.get("/api/live/rerunjob").json()["results"]] == [1]      # task 1 is not the replaced attempt
        (d / "results.jsonl").write_text(rows(1))                       # the runner's rewind drops row 3 (a fresh file)
        st = client.get("/api/live/rerunjob/state").json()
        assert st["done"] == [] and st["has_traj"] is False and st["phase"] == "starting"
        traj.write_text(json.dumps({"messages": [], "info": {}}))       # the new attempt's first model reply
        st = client.get("/api/live/rerunjob/state").json()
        assert st["has_traj"] is True and st["phase"] == "agent" and st["done"] == []
        (d / "results.jsonl").write_text(rows(1, 3))                    # the new attempt is finished
        st = client.get("/api/live/rerunjob/state").json()
        assert st["done"] == [3] and st["cur"] is None and [r["order"] for r in st["results"]] == [3]
        # a chain job never ignores its files (it is not a rerun of one task)
        S.JOBS["rerunjob"]["custom"] = False
        assert S._from_replaced_attempt(S.JOBS["rerunjob"], str(traj)) is False
    finally:
        S.JOBS.pop("rerunjob", None)


def test_live_runs_get_fresh_ids_and_are_listed(monkeypatch, tmp_path):
    """A live run started from the UI (no run id) must never reuse a run id, so earlier live runs are kept on disk."""
    assert S.LiveReq(cid=SHOWCASE, kind="kv").run_id is None
    monkeypatch.setattr(S, "RUNS", tmp_path)
    a = S._new_live_run_id(); assert a.startswith("live_")
    (tmp_path / a).mkdir()
    b = S._new_live_run_id(); assert b != a and b.startswith(a)
    S.JOBS["fakejob3"] = {"id": "fakejob3", "run_id": b, "cid": SHOWCASE, "arm": S.ARMS["kv"], "status": "running"}
    try:
        assert S._new_live_run_id() not in (a, b)
        # the listing: one entry per chain x memory system under live_* / demo_live run ids, recorded runs excluded
        arm = S.ARMS["kv"]; src = ROOT / "runs" / "v3main100" / f"{SHOWCASE}__s0__{arm}"
        import shutil; shutil.copytree(src, tmp_path / b / f"{SHOWCASE}__s0__{arm}")
        os.symlink(ROOT / "runs" / "v3main100", tmp_path / "v3main100")
        r = client.get("/api/liveruns"); assert r.status_code == 200
        ents = r.json(); assert [e["run"] for e in ents] == [b]
        e = ents[0]; assert e["cid"] == SHOWCASE and e["kind"] == "kv" and e["running"] is True and len(e["sessions"]) == 3 and e["resolved"] == 2 and e["usd"] > 0
    finally:
        S.JOBS.pop("fakejob3", None)


# ------------------------------------------------------------------ memory written / saved (KV vs Mem0 view)

def test_parse_items_handles_fifo_cut():
    items, cut = S._parse_items("[a__b-1]\nDID: x\n\n[a__b-2__f1]\nfact one")
    assert items == [("a__b-1", "DID: x"), ("a__b-2__f1", "fact one")] and cut is False
    items, cut = S._parse_items("tail of a cut item\n\n[a__b-3]\nnote")
    assert items == [("a__b-3", "note")] and cut is True
    assert S._parse_items("") == ([], False)


def test_memory_timeline_reconstructed_from_recorded_run():
    kv = client.get(f"/api/memory?run=v3main100&arm={S.ARMS['kv']}&cid={SHOWCASE}").json()
    t1, t2, t3 = kv["tasks"]
    assert t1["written"]["known"] and len(t1["written"]["items"]) == 1 and t1["store"]["src"] == "next_injection"
    assert [i["order"] for i in t2["store"]["items"]] == [1, 2]                  # store after task 2 = notes of tasks 1 and 2
    assert t1["retrieved"]["items"] == [] and [i["order"] for i in t2["retrieved"]["items"]] == [1]   # task 2 was shown task 1's note
    assert [i["order"] for i in t3["retrieved"]["items"]] == [1, 2] and t3["retrieved"]["chars"] == t2["store"]["chars"]
    assert t3["written"]["known"] is False and t3["written"]["nodes"] == 1        # last write: count known, text not logged
    assert kv["exact"] is False and kv["totals"]["store_items"] == 2 and kv["totals"]["writer_tokens"] > 0
    m0 = client.get(f"/api/memory?run=v3main100&arm={S.ARMS['mem0']}&cid={SHOWCASE}").json()
    assert m0["totals"]["empty_writes"] == 3 and m0["totals"]["store_items"] == 0 and m0["tasks"][2]["store"]["src"] == "unchanged"
    assert client.get(f"/api/memory?run=v3main100&arm=nope&cid={SHOWCASE}").status_code == 404


def test_memory_timeline_prefers_saved_store(monkeypatch, tmp_path):
    """Runs launched by the demo save mem_store_<k>.json (harness/mem_snapshot.py): exact, including the last write."""
    import harness.mem_snapshot as MS
    arm = S.ARMS["mem0"]; out = tmp_path / "r1" / f"{SHOWCASE}__s0__{arm}"; out.mkdir(parents=True)
    monkeypatch.setattr(S, "RUNS", tmp_path)
    class Layer:                                   # a store like memory.base.LocalStore
        class store: items = []
    rows = []
    for k, facts in ((1, ["f a", "f b"]), (2, []), (3, ["f c"])):
        Layer.store.items = Layer.store.items + [(f"i-{k}__f{j+1}", f) for j, f in enumerate(facts)]
        MS.save(out, k, Layer, len(facts), force=True)
        rows.append({"order": k, "instance": f"i-{k}", "resolved": k != 2, "gradable": True, "exit": "Submitted", "n_steps": 3, "mem_nodes": len(facts)})
        (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    m = client.get(f"/api/memory?run=r1&arm={arm}&cid={SHOWCASE}").json()
    assert m["exact"] is True and [len(t["written"]["items"]) for t in m["tasks"]] == [2, 0, 1]
    assert m["tasks"][2]["written"]["items"][0]["text"] == "f c" and [i["order"] for i in m["tasks"][2]["store"]["items"]] == [1, 1, 3]
    assert m["totals"]["empty_writes"] == 1 and m["totals"]["store_items"] == 3
    monkeypatch.delenv("RSM_MEM_SAVE", raising=False)
    MS.save(out, 9, Layer, 0)                      # not enabled, not forced: no file
    assert not (out / "mem_store_9.json").exists()


def test_listed_but_refused_model_is_caught_before_a_run(monkeypatch):
    """2026-11-01: claude-sonnet-5 was in the market but the key's policy had no offer for it ("No model offer satisfies the
    effective policy"); family probes (claude-haiku-4-5, gpt-5.2) passed. The status must say so, the UI must not preselect it,
    and a run with it must be refused up front instead of retrying a 400 for minutes."""
    monkeypatch.setenv("GOODVISION_API_KEY", "gv_test")
    nooffer = (400, {"error": {"code": "no_candidate", "message": "No model offer satisfies the effective policy."}})
    g, p = _fake_http(models=("auto", "claude-haiku-4-5", "claude-sonnet-5", "gpt-5.2"), chat={"claude-sonnet-5": nooffer})
    sent = []
    monkeypatch.setattr(PV, "_get", g); monkeypatch.setattr(PV, "_post_json", lambda *a, **k: (sent.append(a[2]["model"]), p(*a, **k))[1])
    monkeypatch.setitem(PV.PROVIDERS["goodvision-test"], "default_model", "claude-sonnet-5")
    monkeypatch.setattr(S, "_PROV_CACHE", {}); monkeypatch.setattr(S, "_MODEL_OK", {})
    c = S._provider_check("goodvision-test")
    assert c["ok"] and c["default_probe"]["ok"] is False and "claude-sonnet-5 refused" in c["message"]
    assert c["default_model"] == "claude-haiku-4-5" and "No model offer" in c["default_model_note"]
    with pytest.raises(S.HTTPException) as e:
        S._provider_env("goodvision-test", "claude-sonnet-5")
    assert e.value.status_code == 400 and "refuses model 'claude-sonnet-5'" in e.value.detail and "gpt-5.2" in e.value.detail
    n = len(sent)
    env, _ = S._provider_env("goodvision-test", "gpt-5.2")         # answered in the family probe: no extra request
    assert len(sent) == n and env


def test_gateway_model_sends_only_role_and_content(monkeypatch):
    """Turn 2+ of a task failed at the SmartRouter because stored replies carried tool_calls/function_call (null) back."""
    sys.path.insert(0, str(ROOT))
    from harness.gateway_model import GatewayTextModel
    m = GatewayTextModel(model_name="openai/auto", model_kwargs={})
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a", "tool_calls": None, "function_call": None, "provider_specific_fields": {"refusal": None}, "extra": {"actions": []}},
            {"role": "user", "content": "obs", "extra": {}}]
    monkeypatch.setenv("RSM_OPENAI_STRICT", "1")
    out = m._prepare_messages_for_api(msgs)
    assert out == [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}, {"role": "user", "content": "obs"}]
    monkeypatch.delenv("RSM_OPENAI_STRICT")
    assert "tool_calls" in m._prepare_messages_for_api(msgs)[2]          # local vLLM path unchanged


# ------------------------------------------------------------------ Azure OpenAI for the GPT models

def test_azure_endpoint_and_gpt_routing(monkeypatch):
    for ep in ("https://res.openai.azure.com", "https://res.openai.azure.com/", "https://res.openai.azure.com/openai/v1/",
               "https://res.openai.azure.com/openai/deployments/x/chat/completions?api-version=2025-01-01"):
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ep)
        assert PV._azure_base() == "https://res.openai.azure.com/openai/v1"
    monkeypatch.setitem(PV.PROVIDERS["azure"], "api_base", "https://res.openai.azure.com/openai/v1")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    assert PV.gpt_route("goodvision-test", "gpt-5.5") == ("goodvision-test", "gpt-5.5")      # Azure not configured: unchanged
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az_test")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENTS", "gpt-5.5=kv-gpt55, gpt-5.4-mini")
    assert PV.gpt_route("goodvision-test", "gpt-5.5") == ("azure", "kv-gpt55")
    assert PV.gpt_route("goodvision-test", "gpt-5.4-mini") == ("azure", "gpt-5.4-mini")
    assert PV.gpt_route("goodvision-test", "auto") == ("goodvision-test", "auto")             # the router keeps auto / Claude
    assert PV.gpt_route("goodvision-test", "claude-sonnet-5") == ("goodvision-test", "claude-sonnet-5")
    assert PV.gpt_route("local", "coder-next") == ("local", "coder-next")
    asked = []                                                                    # unlisted ids: routed only if Azure has them
    monkeypatch.setattr(PV, "_AZ_HAS", {})
    monkeypatch.setattr(PV, "probe_model", lambda n, m, *a: (asked.append(m), {"ok": m == "gpt-5.4"})[1])
    assert PV.gpt_route("goodvision-test", "gpt-5.4") == ("azure", "gpt-5.4")
    assert PV.gpt_route("goodvision-test", "gpt-5.2") == ("goodvision-test", "gpt-5.2")   # DeploymentNotFound: stays on the router
    assert PV.gpt_route("goodvision-test", "gpt-5.4") == ("azure", "gpt-5.4") and asked == ["gpt-5.4", "gpt-5.2"]   # cached
    monkeypatch.setenv("DEMO_GPT_VIA_AZURE", "0")
    assert PV.gpt_route("goodvision-test", "gpt-5.5") == ("goodvision-test", "gpt-5.5")
    env = PV.env_for_harness("azure", "kv-gpt55")
    assert env["RSM_API_BASE"].endswith("/openai/v1") and env["RSM_API_KEY"] == "az_test" and env["RSM_OPENAI_STRICT"] == "1" and env["RSM_WRITER_MODEL"] == "kv-gpt55"


def test_azure_check_probes_the_default_deployment(monkeypatch):
    monkeypatch.setitem(PV.PROVIDERS["azure"], "default_model", "gpt-5.5")
    monkeypatch.setitem(PV.PROVIDERS["azure"], "api_base", "https://res.openai.azure.com/openai/v1")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az_test"); monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENTS", "gpt-5.5=kv-gpt55")
    sent = []
    g, p = _fake_http(models=("gpt-5.5-2026-04-24", "gpt-4o"))               # Azure /models lists base models, not deployments
    monkeypatch.setattr(PV, "_get", g); monkeypatch.setattr(PV, "_post_json", lambda *a, **k: (sent.append(a[2]["model"]), p(*a, **k))[1])
    c = PV.check("azure")
    assert c["ok"] and c["default_probe"]["ok"] and sent == ["kv-gpt55"] and "families" not in c
    monkeypatch.setattr(S, "_PROV_CACHE", {}); monkeypatch.setattr(S, "_MODEL_OK", {})
    env, base = S._provider_env("azure", "kv-gpt55")                            # deployment not in /models: still allowed
    assert base.endswith("/openai/v1") and env["RSM_API_KEY"] == "az_test"


# ------------------------------------------------------------------ stopping live runs

def test_stop_kills_the_process_group_and_reports_stopped(monkeypatch, tmp_path):
    import subprocess, sys, time as _t
    marker = tmp_path / "child.pid"
    code = f"import subprocess,sys,time; c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); open({str(marker)!r},'w').write(str(c.pid)); time.sleep(60)"
    p = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    monkeypatch.setitem(S.JOBS, "jstop", {"id": "jstop", "status": "running", "pid": p.pid, "started": _t.time()})
    for _ in range(50):
        if marker.exists() and marker.read_text():
            break
        _t.sleep(0.1)
    child = int(marker.read_text())
    r = client.post("/api/live/stop", json={"jobs": ["jstop", "nosuchjob"]}).json()
    assert r["stopped"] == ["jstop"]
    rc = p.wait(timeout=10)
    S.JOBS["jstop"].update(status=S._end_status("jstop", rc))
    assert S.JOBS["jstop"]["status"] == "stopped"
    for _ in range(50):                                   # the grandchild (a sandbox command) dies with the group
        try:
            os.kill(child, 0); _t.sleep(0.1)
        except ProcessLookupError:
            break
    else:
        raise AssertionError("grandchild survived the stop")
    assert client.post("/api/live/stop", json={"jobs": ["jstop"]}).json()["stopped"] == []   # not running any more
    assert client.post("/api/live/nosuchjob/stop").status_code == 404


def test_notes_read_as_plain_sentences():
    """The pages show a Knowledge Vault version's note as '<note>. Used for new live, compare and sandbox runs; …' and a
    memory method's note after 'Name: ' / 'Name — ': a capitalised sentence without a trailing period or a prompt file
    name, and a lowercase fragment without a colon."""
    import re
    for v in client.get("/api/meta").json()["kv_versions"]:
        n = v["note"]
        assert n[0].isupper() and not n.endswith(".") and "writer_prompt" not in n and not re.search(r"\w\.(txt|md|py|json)\b", n), v
    notes = client.get("/api/showcase").json()["kind_notes"]
    assert all(n[0].islower() and ":" not in n and not n.endswith(".") for n in notes.values()), notes
    assert notes["kv"].startswith("ours — ") and "writer distils" in notes["kv"]


def test_pages_and_static_revalidate():
    for path in ("/static/player.js", "/compare", "/"):
        assert client.get(path).headers.get("cache-control") == "no-cache", path


# ------------------------------------------------------------------ recorded-only kinds (MemOp, full history) and showcase chains

def test_kind_of_arm_never_takes_memop_for_kv():
    assert S._kind_of_arm("mem_memop") == "memop" and S._kind_of_arm("mem_rawcat") == "rawcat"
    assert [S._kind_of_arm(a) for a in ("mem_fusednote", "mem_v5inline", "mem_factsall", "seq")] == ["kv", "kv", "mem0", "none"]
    assert set(S.ARMS) == {"none", "mem0", "kv"} and S.ALL_ARMS == {**S.ARMS, "memop": "mem_memop", "rawcat": "mem_rawcat"}


def test_memop_writer_is_estimated_and_rawcat_has_no_writer():
    mo = S.load_chain_arm("v3main100", "mem_memop", SHOWCASE)
    assert mo["kind"] == "memop" and len(mo["sessions"]) == 3
    assert all(s["tok"]["writer_src"] == "estimate" and s["tok"]["writer_prompt"] > 0 for s in mo["sessions"])
    s1, s2, s3 = mo["sessions"]
    assert s1["note_written"] == s2["memory_text"].strip() and s1["tok"]["writer_completion"] > 0   # the whole rewritten guidance
    assert s3["note_written"] == "" and s3["tok"]["writer_completion"] == 0                          # nobody reads the last rewrite
    assert s2["tok"]["writer_prompt"] > S.L._tok_est(s2["memory_text"])                             # material + prompt + previous guidance
    rc = S.load_chain_arm("v3main100", "mem_rawcat", SHOWCASE)
    assert rc["kind"] == "rawcat" and all(s["tok"]["writer_prompt"] == s["tok"]["writer_completion"] == 0 and s["tok"]["writer_src"] == "none"
                                          for s in rc["sessions"])
    assert S.load_chain_arm("v3main100", "mem_fusednote", SHOWCASE)["kind"] == "kv"


def test_runchains_kinds_and_single_chain():
    base = client.get("/api/runchains", params={"run": "v3main100", "cid": SHOWCASE}).json()
    assert [c["cid"] for c in base["chains"]] == [SHOWCASE] and list(base["chains"][0]["arms"]) == ["none", "mem0", "kv"]
    full = client.get("/api/runchains", params={"run": "v3main100", "cid": SHOWCASE, "kinds": "kv,mem0,memop,rawcat,none"}).json()
    arms = full["chains"][0]["arms"]
    assert list(arms) == ["none", "mem0", "kv", "memop", "rawcat"] and arms["memop"]["arm"] == "mem_memop" and arms["rawcat"]["arm"] == "mem_rawcat"
    assert arms["kv"] == base["chains"][0]["arms"]["kv"] and arms["memop"]["resolved"] == 3
    r = client.get("/api/runchains", params={"run": "v3main100", "kinds": "kv,bogus"})
    assert r.status_code == 400 and "bogus" in r.json()["detail"]
    assert client.get("/api/runchains", params={"run": "v3main100", "cid": "nope"}).json()["chains"] == []
    only = client.get("/api/runchains", params={"run": "v3main100", "kinds": "memop"}).json()["chains"]
    assert len(only) == 100 and all(list(c["arms"]) == ["memop"] for c in only)


def test_showcase_chains_all_show_kv_saving_tokens():
    sc = client.get("/api/showcase").json()
    cfg = json.load(open(ROOT / "demo" / "configs.json"))
    assert sc["run"] == "v3main100" and sc["margin"] == 0.2 and sc["default_cid"] == DEFAULT_CID and sc["price_row"]
    assert client.get("/api/meta").json()["default_cid"] == DEFAULT_CID == cfg["showcase_default"]
    assert sc["kinds"] == {"kv": "Knowledge Vault", "mem0": "Mem0", "memop": "MemOp", "rawcat": "Full history", "none": "No memory"}
    assert sc["arms"]["memop"] == "mem_memop" and set(sc["kind_notes"]) == set(sc["kinds"])
    assert [c["cid"] for c in sc["chains"]] == [e["cid"] for e in cfg["showcase_chains"]] and len(sc["chains"]) == 8
    for c in sc["chains"]:
        st_ = c["stats"]
        assert set(st_) == set(sc["kinds"]) and c["title"] and c["blurb"] and c["repo"] and c["n_tasks"] >= 3
        # Knowledge Vault wins against every other method, MemOp included (Peter, 2026-11-13)
        assert st_["kv"]["tokens"] <= (1 - sc["margin"]) * min(st_["mem0"]["tokens"], st_["memop"]["tokens"], st_["none"]["tokens"]), c["cid"]
        assert st_["kv"]["resolved"] >= max(1, st_["mem0"]["resolved"], st_["memop"]["resolved"], st_["none"]["resolved"]), c["cid"]
    assert sc["chains"][0]["cid"] == DEFAULT_CID and SHOWCASE not in [c["cid"] for c in sc["chains"]]
    # pyccel left the list: MemOp used fewer tokens there. Its numbers still come from the recorded run (/api/runchains)
    py = {"cid": SHOWCASE, "stats": {k: {"tokens": sum(s["tokens"] for s in a["sessions"]), "resolved": sum(1 for s in a["sessions"] if s["resolved"]),
                                         "sessions": len(a["sessions"]), "usd": a["usd"]}
                                     for k, a in client.get("/api/runchains", params={"run": "v3main100", "cid": SHOWCASE, "kinds": "kv,mem0,memop,rawcat,none"}).json()["chains"][0]["arms"].items()}}
    kv, m0, no = py["stats"]["kv"], py["stats"]["mem0"], py["stats"]["none"]
    assert (kv["resolved"], m0["resolved"], no["resolved"], kv["sessions"]) == (2, 1, 1, 3)
    assert abs(kv["tokens"] - 593_000) < 5_000 and abs(m0["tokens"] - 1_890_000) < 10_000 and abs(no["tokens"] - 2_510_000) < 10_000
    assert py["stats"]["memop"]["tokens"] < kv["tokens"]           # why the compare page narrows the list when MemOp is shown
    md = sc["chains"][0]                                          # the default chain: its card numbers are exactly what runchains' sessions sum to
    rc = client.get("/api/runchains", params={"run": "v3main100", "cid": DEFAULT_CID, "kinds": "kv,mem0,memop,rawcat,none"}).json()
    for k, a in rc["chains"][0]["arms"].items():
        assert md["stats"][k]["tokens"] == sum(s["tokens"] for s in a["sessions"]) and md["stats"][k]["usd"] == pytest.approx(a["usd"], abs=1e-3)
    rows = [r for r in json.load(open(S.L.price_table_path()))["rows"] if r != sc["price_row"]]
    other = client.get("/api/showcase", params={"price": rows[0]}).json()
    assert other["price_row"] == rows[0] and other["chains"][0]["stats"]["kv"]["tokens"] == md["stats"]["kv"]["tokens"]
    assert other["chains"][0]["stats"]["kv"]["usd"] != md["stats"]["kv"]["usd"]


def test_session_memory_and_chain_for_memop_and_rawcat():
    s = client.get("/api/session", params={"run": "v3main100", "arm": "mem_memop", "cid": SHOWCASE, "order": 2}).json()
    assert s["kind"] == "memop" and s["memory"].startswith("## Task Guidance") and s["turns"] and s["tok"]["writer_src"] == "estimate"
    r = client.get("/api/session", params={"run": "v3main100", "arm": "mem_rawcat", "cid": SHOWCASE, "order": 2}).json()
    assert r["kind"] == "rawcat" and r["memory"] and r["tok"]["writer_prompt"] == 0
    m = client.get(f"/api/memory?run=v3main100&arm=mem_memop&cid={SHOWCASE}").json()
    assert m["kind"] == "memop" and len(m["tasks"]) == 3 and m["totals"]["writer_tokens"] > 0
    assert m["tasks"][0]["written"]["known"] and m["tasks"][1]["retrieved"]["chars"] > 0
    mr = client.get(f"/api/memory?run=v3main100&arm=mem_rawcat&cid={SHOWCASE}").json()
    assert mr["kind"] == "rawcat" and mr["totals"]["writer_tokens"] == 0 and mr["tasks"][1]["retrieved"]["items"]
    ch = client.get(f"/api/chain/{SHOWCASE}", params={"run": "v3main100"}).json()
    assert ch["arms"]["mem_memop"]["kind"] == "memop" and ch["arms"]["mem_rawcat"]["kind"] == "rawcat" and ch["arms"]["kv"]["kind"] == "kv"


# ------------------------------------------------------------------ sandbox projects: empty folder, your files, your git repo

@pytest.fixture
def sandbox_env(monkeypatch, tmp_path):
    """Scratch projects.json and workspaces, a fake generic image (sparse 3.12.sif) and the showcase chain's image."""
    sifs = tmp_path / "sifs"
    sifs.mkdir()
    for n in ("3.12", S.CHAINS[SHOWCASE]["tag"]):
        with open(sifs / f"{n}.sif", "wb") as f:
            f.truncate(100_000_001)                          # sparse: _sandboxes only looks at the size
    monkeypatch.setattr(S, "SIF_DIR", sifs)
    monkeypatch.setattr(S, "PROJECTS_FILE", tmp_path / "runs_custom" / "projects.json")
    monkeypatch.setattr(S, "CUSTOM_WORK", tmp_path / "work")
    monkeypatch.setattr(S, "CHAINS", dict(S.CHAINS))
    monkeypatch.setattr(S, "_host_addresses", lambda host, port: ["140.82.112.3"])   # public DNS answer, offline
    for k in ("DEMO_GIT_ALLOW_FILE", "DEMO_SEED_MAX_MB", "DEMO_UPLOAD_MAX_MB"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True).stdout


def _bare_repo(tmp):
    """A local bare repo 'myrepo.git': branch main (README.md, src/app.py) and branch dev (+ DEV.md)."""
    w = tmp / "src_repo"
    (w / "src").mkdir(parents=True)
    (w / "README.md").write_text("hello\n"); (w / "src" / "app.py").write_text("print(1)\n")
    _git(w, "init", "-q", "-b", "main"); _git(w, "add", "-A"); _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    _git(w, "checkout", "-q", "-b", "dev"); (w / "DEV.md").write_text("dev\n"); _git(w, "add", "-A")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "dev"); _git(w, "checkout", "-q", "main")
    _git(tmp, "clone", "-q", "--bare", str(w), str(tmp / "myrepo.git"))
    return tmp / "myrepo.git"


def test_project_create_names_sources_and_default_image(sandbox_env):
    sb = client.get("/api/sandboxes").json()
    assert [s["id"] for s in sb if s.get("default")] == ["3.12"] and {s["kind"] for s in sb} == {"generic", "repo"}
    assert {s["id"]: s["label"] for s in sb} == {"3.12": "Python 3.12 — an empty folder with git",
                                                  S.CHAINS[SHOWCASE]["tag"]: "pyccel/pyccel — the repository at the commit its ChainSWE tasks start from"}
    r = client.post("/api/custom/project", json={"source": "empty", "hint": "Write a tiny CLI that prints the date, please"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["name"] == "write-a-tiny-cli-that" and p["sandbox"] == "3.12" and p["source"] == {"kind": "empty", "files": 0}
    assert p["label"] == "Empty folder · Python 3.12" and (sandbox_env / "work" / p["name"]).is_dir()
    p2 = client.post("/api/custom/project", json={"source": "upload", "hint": "Write a tiny CLI that prints the time"}).json()
    assert p2["name"] == "write-a-tiny-cli-that-2" and p2["label"] == "Your files · Python 3.12" and p2["source"]["kind"] == "upload"
    assert client.post("/api/custom/project", json={}).json()["name"] == "project"
    assert client.post("/api/custom/project", json={"hint": "¿¡!"}).json()["name"] == "project-2"
    r = client.post("/api/custom/project", json={"name": "write-a-tiny-cli-that", "source": "empty"})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    assert client.post("/api/custom/project", json={"name": SHOWCASE}).status_code == 400
    assert client.post("/api/custom/project", json={"name": ".."}).status_code == 400            # never a dot directory
    assert S._project_name("../../etc") == "etc" and S._project_name(".hidden.") == "hidden" and S._project_name("a b/c") == "a-b-c"
    assert [S._image_desc(x) for x in ("3.12", "sandbox-python-3.12", "sandbox-python-git", "node")] == ["Python 3.12", "Python 3.12", "python-git", "node"]
    old = client.post("/api/custom/project", json={"name": "old-style", "sandbox": "3.12"}).json()          # older clients
    assert old["source"] == {"kind": "empty", "files": 0} and old["sandbox"] == "3.12"
    ex = client.post("/api/custom/project", json={"name": "ex", "sandbox": S.CHAINS[SHOWCASE]["tag"]}).json()
    assert ex["source"] == {"kind": "image", "chain": SHOWCASE} and ex["label"] == "Example repo · pyccel/pyccel"
    assert client.post("/api/custom/project", json={"source": "image"}).status_code == 400
    assert client.post("/api/custom/project", json={"source": "empty", "sandbox": S.CHAINS[SHOWCASE]["tag"]}).status_code == 400
    assert client.post("/api/custom/project", json={"source": "bogus"}).status_code == 400
    ps = json.load(open(S.PROJECTS_FILE))
    # made before sources existed: labels from the image list, shown in plain words, projects.json left as it is
    ps["legacy"] = {"sandbox": "3.12", "label": "3.12 — empty workspace (/work, git initialised)", "created": "x", "tasks": []}
    ps["legacy-ex"] = {"sandbox": S.CHAINS[SHOWCASE]["tag"], "label": f"pyccel/pyccel — repo at the chain's base commit ({SHOWCASE})", "created": "x", "tasks": []}
    ps["legacy-gone"] = {"sandbox": "gone-image", "label": "acme/thing — repo at the chain's base commit (acme_thing_2020)", "created": "x", "tasks": []}
    S._save_projects(ps)
    raw = S.PROJECTS_FILE.read_bytes()
    lst = {p["name"]: p for p in client.get("/api/custom/projects").json()}
    assert lst["legacy"]["source"] == {"kind": "empty"} and lst["ex"]["source"]["kind"] == "image" and lst["project"]["downloads"] == []
    assert (lst["legacy"]["label"], lst["legacy-ex"]["label"], lst["legacy-gone"]["label"]) == ("Empty folder · Python 3.12", "Example repo · pyccel/pyccel", "Example repo · acme/thing")
    assert lst["write-a-tiny-cli-that-2"]["label"] == "Your files · Python 3.12" and not any(" — " in p["label"] for p in lst.values())
    assert S.PROJECTS_FILE.read_bytes() == raw
    S._register_custom_chains()                                   # the session header / compare page read the chain's repo
    assert S.CHAINS["legacy-ex"]["repo"] == "Example repo · pyccel/pyccel" and S.CHAINS["legacy"]["repo"] == "Empty folder · Python 3.12"
    os.unlink(sandbox_env / "sifs" / "3.12.sif")
    r = client.post("/api/custom/project", json={"name": "nogeneric"})
    assert r.status_code == 400 and "generic sandbox image" in r.json()["detail"]


def test_project_from_a_git_repo(sandbox_env, monkeypatch):
    url = f"file://{_bare_repo(sandbox_env)}"
    r = client.post("/api/custom/project", json={"source": "git", "git_url": url})
    assert r.status_code == 400 and "https://" in r.json()["detail"]                 # file:// only with the test-only flag
    monkeypatch.setenv("DEMO_GIT_ALLOW_FILE", "1")
    r = client.post("/api/custom/project", json={"source": "git", "git_url": url})
    assert r.status_code == 200, r.text
    p = r.json()
    seed = sandbox_env / "work" / "myrepo" / "seed"
    assert p["name"] == "myrepo" and p["source"]["kind"] == "git" and p["source"]["url"] == url and len(p["source"]["commit"]) == 40
    assert p["label"] == "myrepo (local) · Python 3.12" and p["source"]["files"] == 0
    assert (seed / "README.md").read_text() == "hello\n" and (seed / ".git").is_dir() and not (seed / "DEV.md").exists()
    assert _git(seed, "rev-parse", "HEAD").strip() == p["source"]["commit"] and _git(seed, "rev-list", "--count", "HEAD").strip() == "1"
    b = client.post("/api/custom/project", json={"source": "git", "git_url": url, "git_ref": "dev"}).json()
    assert b["name"] == "myrepo-2" and b["source"]["ref"] == "dev" and "@dev" in b["label"]
    assert (sandbox_env / "work" / "myrepo-2" / "seed" / "DEV.md").exists()
    before = json.load(open(S.PROJECTS_FILE))
    r = client.post("/api/custom/project", json={"source": "git", "git_url": f"file://{sandbox_env}/nope.git"})
    assert r.status_code == 400 and r.json()["detail"].startswith("git clone failed: ") and "Could not read from remote repository" in r.json()["detail"]   # no known cause: git's last words
    assert json.load(open(S.PROJECTS_FILE)) == before and not (sandbox_env / "work" / "nope").exists()   # nothing left behind
    r = client.post("/api/custom/project", json={"source": "git", "git_url": url, "git_ref": "no-such-branch"})
    assert r.status_code == 400 and not (sandbox_env / "work" / "myrepo-3").exists()
    assert r.json()["detail"] == "git clone failed: no branch or tag named no-such-branch in this repository"
    monkeypatch.setenv("DEMO_SEED_MAX_MB", "0.00001")
    r = client.post("/api/custom/project", json={"source": "git", "git_url": url, "name": "toobig"})
    assert r.status_code == 400 and "larger than" in r.json()["detail"] and not (sandbox_env / "work" / "toobig").exists()
    assert json.load(open(S.PROJECTS_FILE)) == before


@pytest.mark.parametrize("url,ref,why", [
    ("http://github.com/o/r", None, "https://"), ("ftp://github.com/o/r", None, "https://"), ("file:///tmp/x.git", None, "https://"),
    ("https://localhost/o/r", None, "local addresses"), ("https://127.0.0.1/o/r", None, "private or local"),
    ("https://10.1.2.3/o/r", None, "private or local"), ("https://192.168.0.5:8443/o/r", None, "private or local"),
    ("https://169.254.169.254/latest", None, "private or local"), ("https://[::1]/o/r", None, "private or local"),
    ("https://[::ffff:127.0.0.1]/o/r", None, "private or local"), ("https://internal.example/o/r", None, "private or local"),
    ("https://github.com/o/r x", None, "spaces"), ("https://github.com/" + "a" * 500, None, "too long"),
    ("https://github.com", None, "repository path"), ("https://github.com/o/r?x=1", None, "plain repository URL"),
    ("https://github.com/o/r", "-upload-pack=x", "branch"), ("https://github.com/o/r", "a b", "branch"), ("https://github.com/o/r", "$(id)", "branch"),
    ("localhost/o/r", None, "local addresses"), ("10.1.2.3/o/r", None, "private or local"), ("[::1]:8080/o/r", None, "private or local"),
    ("internal.example/o/r", None, "private or local"), ("owner/repo", None, "https://"), ("git@github.com:o/r.git", None, "https://"),
])
def test_git_url_rules(sandbox_env, monkeypatch, url, ref, why):
    monkeypatch.setattr(S, "_host_addresses", lambda host, port: ["10.0.0.7"] if host == "internal.example" else ["140.82.112.3"])
    r = client.post("/api/custom/project", json={"source": "git", "git_url": url, "git_ref": ref})
    assert r.status_code == 400 and why in r.json()["detail"], r.text
    assert not S.PROJECTS_FILE.exists() and not any((sandbox_env / "work").glob("*/seed"))


def test_git_credentials_are_used_but_never_stored(sandbox_env, monkeypatch):
    bare = _bare_repo(sandbox_env)
    seen = {}

    def fake_popen(cmd, env=None, **kw):                 # the network clone, replaced by a local one; records what git got
        seen["cmd"], seen["env"] = list(cmd), dict(env)
        local = list(cmd)
        local[local.index("--") + 1] = f"file://{bare}"
        return subprocess.Popen(["git", "-c", "protocol.file.allow=always", *local[1:]], env=env, **kw)
    monkeypatch.setattr(S, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": fake_popen}))
    url = "https://alice:s3cr3t-T0KEN@github.com/acme/widget.git"
    r = client.post("/api/custom/project", json={"source": "git", "git_url": url})
    assert r.status_code == 200, r.text
    p = r.json()
    assert "s3cr3t" not in r.text and p["name"] == "widget" and p["source"]["url"] == "https://github.com/acme/widget.git"
    assert p["label"] == "github.com/acme/widget · Python 3.12"
    assert not any("s3cr3t" in c for c in seen["cmd"]) and seen["cmd"][seen["cmd"].index("--") + 1] == "https://github.com/acme/widget.git"
    assert seen["env"]["KVGIT_PASS"] == "s3cr3t-T0KEN" and seen["env"]["KVGIT_USER"] == "alice" and seen["env"]["KVGIT_HOST"] == "github.com"
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and seen["env"]["GIT_CONFIG_GLOBAL"] == os.devnull and "GIT_ASKPASS" not in seen["env"]
    assert "s3cr3t" not in S.PROJECTS_FILE.read_text() and "s3cr3t" not in (sandbox_env / "work" / "widget" / "seed" / ".git" / "config").read_text()

    def failing(cmd, env=None, **kw):
        return subprocess.Popen(["sh", "-c", "echo 'fatal: Authentication failed for https://alice:s3cr3t-T0KEN@github.com/acme/widget.git/' >&2; exit 128"], env=env, **kw)
    monkeypatch.setattr(S, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": failing}))
    r = client.post("/api/custom/project", json={"source": "git", "git_url": url, "name": "w2"})
    assert r.status_code == 400 and "s3cr3t" not in r.text
    assert r.json()["detail"] == "git clone failed: the repository does not exist or is private — only public repositories can be cloned without a token"


def test_a_git_url_without_https_is_read_as_https(sandbox_env, monkeypatch):
    """"github.com/owner/repo" as copied from an address bar is cloned as https://github.com/owner/repo."""
    g = S._check_git("github.com/octocat/Hello-World", None)
    assert (g["clone_url"], g["where"], g["repo"]) == ("https://github.com/octocat/Hello-World", "github.com/octocat/Hello-World", "Hello-World")
    assert S._check_git("www.github.com:443/o/r.git", "main")["clean_url"] == "https://www.github.com:443/o/r.git"
    bare, seen = _bare_repo(sandbox_env), {}

    def fake_popen(cmd, env=None, **kw):                 # the network clone, replaced by a local one
        seen["url"] = cmd[cmd.index("--") + 1]
        local = list(cmd)
        local[local.index("--") + 1] = f"file://{bare}"
        return subprocess.Popen(["git", "-c", "protocol.file.allow=always", *local[1:]], env=env, **kw)
    monkeypatch.setattr(S, "subprocess", types.SimpleNamespace(**{**vars(subprocess), "Popen": fake_popen}))
    p = client.post("/api/custom/project", json={"source": "git", "git_url": "github.com/acme/widget"}).json()
    assert seen["url"] == "https://github.com/acme/widget" and p["source"]["url"] == "https://github.com/acme/widget"
    assert p["name"] == "widget" and p["label"] == "github.com/acme/widget · Python 3.12"


def test_a_busy_method_is_refused_in_plain_words(sandbox_env, monkeypatch):
    monkeypatch.delenv("DEMO_DISABLE_LIVE", raising=False)
    client.post("/api/custom/project", json={"source": "empty", "name": "busy"})
    S.JOBS["busyjob"] = {"id": "busyjob", "cid": "busy", "kind": "mem0", "arm": "mem_factsall", "custom": True, "status": "running", "order": 1}
    try:
        r = client.post("/api/custom/start", json={"project": "busy", "task": "add a file", "kinds": ["mem0"]})
        assert r.status_code == 409 and r.json()["detail"] == "The Mem0 run of this project is still going — wait for it or stop it"
        assert json.load(open(S.PROJECTS_FILE))["busy"]["tasks"] == []          # refused before the task is added
    finally:
        S.JOBS.pop("busyjob", None)


def test_git_credential_helper_answers_only_for_its_host(sandbox_env):
    env = S._git_env({"KVGIT_HOST": "github.com", "KVGIT_USER": "alice", "KVGIT_PASS": "pw with spaces"})
    cmd = ["git", "-c", "credential.helper=", "-c", "credential.helper=" + S._GIT_CRED_HELPER, "credential", "fill"]
    ok = subprocess.run(cmd, input="protocol=https\nhost=github.com\n\n", env=env, capture_output=True, text=True)
    assert ok.returncode == 0 and "username=alice\n" in ok.stdout and "password=pw with spaces\n" in ok.stdout
    other = subprocess.run(cmd, input="protocol=https\nhost=evil.example\n\n", env=env, capture_output=True, text=True)
    assert other.returncode != 0 and "pw with spaces" not in other.stdout + other.stderr


def _upload(name, path, data, **params):
    return client.post(f"/api/custom/project/{name}/files", params={"path": path, **params}, content=data,
                       headers={"Content-Type": "application/octet-stream"})


def _zip(entries):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, data in entries:              # name: a member path or a ZipInfo (e.g. a symlink entry)
            z.writestr(name, data)
    return b.getvalue()


def test_uploads_go_to_the_seed_then_to_every_workspace(sandbox_env, monkeypatch):
    client.post("/api/custom/project", json={"source": "upload", "name": "up"})
    work = sandbox_env / "work" / "up"
    seed = work / "seed"
    r = _upload("up", "data/input.csv", b"a,b\n1,2\n")
    assert r.status_code == 200, r.text
    assert r.json() == {"path": "data/input.csv", "bytes": 8, "files": 1, "workspaces": 0}
    assert (seed / "data" / "input.csv").read_bytes() == b"a,b\n1,2\n"
    assert _upload("up", "./notes//todo.txt", b"x").json()["path"] == "notes/todo.txt"
    for bad in ("../evil.txt", "a/../../evil.txt", "/etc/passwd", "a\\b.txt", "", "a/\x01.txt", "x" * 401, "./.."):
        assert _upload("up", bad, b"x").status_code == 400, bad
    assert not (work / "evil.txt").exists() and not (sandbox_env / "work" / "evil.txt").exists()
    assert _upload("up", ".git/config", b"[core]\n").status_code == 200            # before the first task a folder may carry its history
    link = zipfile.ZipInfo("proj-main/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    z = _zip([("proj-main/", ""), ("proj-main/app.py", "print('hi')\n"), ("proj-main/pkg/mod.py", "X = 1\n"),
              ("__MACOSX/proj-main/._app.py", "junk"), (link, "/etc/passwd")])
    r = _upload("up", "vendor/proj.zip", z, unzip=1)
    assert r.status_code == 200, r.text
    assert r.json() == {"path": "vendor/proj.zip", "bytes": 18, "files": 2, "skipped": 1, "unzipped": True, "workspaces": 0}
    assert (seed / "vendor" / "app.py").read_text() == "print('hi')\n" and (seed / "vendor" / "pkg" / "mod.py").read_text() == "X = 1\n"
    assert not (seed / "vendor" / "proj.zip").exists() and not os.path.lexists(seed / "vendor" / "link") and not (seed / "__MACOSX").exists()
    r = _upload("up", "slip.zip", _zip([("ok.txt", "fine"), ("../../escape.txt", "bad")]), unzip=1)
    assert r.status_code == 400 and "unsafe path" in r.json()["detail"]
    assert not (seed / "ok.txt").exists() and not (sandbox_env / "escape.txt").exists() and not (work / "escape.txt").exists()
    assert _upload("up", "kept.zip", _zip([("a.txt", "a")])).status_code == 200 and (seed / "kept.zip").exists()   # no unzip=1: kept
    assert json.load(open(S.PROJECTS_FILE))["up"]["source"]["files"] == 6
    # once a memory system has a workspace: the seed and the workspace both get the file; .git is refused; links are not followed
    ws = work / "mem_fusednote" / "repo"
    ws.mkdir(parents=True)
    (ws / "outside").symlink_to(sandbox_env)
    r = _upload("up", "later.txt", b"new")
    assert r.status_code == 200 and r.json()["workspaces"] == 1
    assert (ws / "later.txt").read_bytes() == b"new" and (seed / "later.txt").read_bytes() == b"new"
    r = _upload("up", ".git/hooks/post-commit", b"#!/bin/sh\n")
    assert r.status_code == 400 and ".git" in r.json()["detail"]
    r = _upload("up", "outside/pwned.txt", b"x")
    assert r.status_code == 400 and not (sandbox_env / "pwned.txt").exists() and not (seed / "outside").exists()
    r = _upload("up", "repo.zip", _zip([("r/.git/HEAD", "ref: refs/heads/main\n"), ("r/main.py", "1\n")]), unzip=1)
    assert r.status_code == 200 and r.json()["files"] == 1 and r.json()["skipped"] == 1 and (ws / "main.py").exists() and not (ws / ".git").exists()
    # limits: 413 and nothing left behind
    monkeypatch.setenv("DEMO_UPLOAD_MAX_MB", "0.001")
    r = _upload("up", "big.bin", b"x" * 5000)
    assert r.status_code == 413 and not (seed / "big.bin").exists() and not (ws / "big.bin").exists()
    r = client.post("/api/custom/project/up/files", params={"path": "big2.bin"}, content=iter([b"x" * 800, b"y" * 800]))   # chunked: no length
    assert r.status_code == 413 and not (seed / "big2.bin").exists()
    assert not [p for p in (sandbox_env / "work" / ".incoming").iterdir() if p.suffix == ".part"]
    monkeypatch.setenv("DEMO_UPLOAD_MAX_MB", "50")
    monkeypatch.setenv("DEMO_SEED_MAX_MB", "0.001")
    assert _upload("up", "big.bin", b"x" * 5000).status_code == 413 and not (seed / "big.bin").exists()
    assert _upload("up", "big.zip", _zip([("big.txt", "x" * 5000)]), unzip=1).status_code == 413 and not (seed / "big.txt").exists()
    monkeypatch.delenv("DEMO_SEED_MAX_MB")
    monkeypatch.setattr(S, "UPLOAD_MAX_FILES", 9)
    assert _upload("up", "many.zip", _zip([(f"f{i}.txt", "x") for i in range(3)]), unzip=1).status_code == 413
    monkeypatch.setitem(S.JOBS, "busyjob", {"id": "busyjob", "custom": True, "cid": "up", "status": "running"})
    assert _upload("up", "a.txt", b"x").status_code == 409
    client.post("/api/custom/project", json={"name": "ex", "sandbox": S.CHAINS[SHOWCASE]["tag"]})
    r = _upload("ex", "a.txt", b"x")
    assert r.status_code == 400 and "empty, upload and git" in r.json()["detail"]
    assert _upload("nope", "a.txt", b"x").status_code == 404


def test_download_zips_a_workspace_at_head_without_running_its_git_config(sandbox_env):
    client.post("/api/custom/project", json={"name": "dl"})
    ws = sandbox_env / "work" / "dl" / "mem_fusednote" / "repo"
    (ws / "sub").mkdir(parents=True)
    (ws / "app.py").write_text("print(1)\n"); (ws / "sub" / "x.txt").write_text("x\n"); (ws / ".gitattributes").write_text("*.py filter=evil\n")
    _git(ws, "init", "-q"); _git(ws, "add", "-A"); _git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "rsm: task 1")
    marker = sandbox_env / "repo-config-ran"
    _git(ws, "config", "filter.evil.smudge", f"touch {marker}; cat"); _git(ws, "config", "core.fsmonitor", f"touch {marker}")
    (ws / "uncommitted.txt").write_text("not yet\n")
    r = client.get("/api/custom/project/dl/download", params={"kind": "kv"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip" and r.headers["content-disposition"].startswith("attachment") and 'filename="dl-kv.zip"' in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"app.py", "sub/x.txt", ".gitattributes"} <= set(zf.namelist()) and "uncommitted.txt" not in zf.namelist()
    assert zf.read("app.py") == b"print(1)\n" and not marker.exists()        # the agent-writable git config never runs on the host
    assert not [p for p in (sandbox_env / "work" / ".incoming").iterdir() if p.suffix == ".zip"]       # temp zip removed after sending
    assert client.get("/api/custom/project/dl/download", params={"kind": "mem0"}).status_code == 404
    assert client.get("/api/custom/project/dl/download", params={"kind": "bogus"}).status_code == 400
    assert client.get("/api/custom/project/nope/download", params={"kind": "kv"}).status_code == 404
    assert {p["name"]: p for p in client.get("/api/custom/projects").json()}["dl"]["downloads"] == ["kv"]
    # rename / delete carry the seed and the workspaces along (all of it lives under CUSTOM_WORK/<name>/)
    assert _upload("dl", "seedfile.txt", b"s").status_code == 200
    assert client.post("/api/custom/project/rename", json={"name": "dl", "new_name": "dl2"}).status_code == 200
    assert (sandbox_env / "work" / "dl2" / "seed" / "seedfile.txt").exists() and not (sandbox_env / "work" / "dl").exists()
    assert client.get("/api/custom/project/dl2/download", params={"kind": "kv"}).status_code == 200
    assert client.get("/api/custom/project/dl/download", params={"kind": "kv"}).status_code == 404
    assert client.delete("/api/custom/project/dl2").status_code == 200 and not (sandbox_env / "work" / "dl2").exists()


RUNNER_PROBE = r'''
import json, os, pathlib, subprocess, sys
sys.path.insert(0, sys.argv[1] + "/demo"); sys.path.insert(0, sys.argv[1])
import custom_runner as CR
T = pathlib.Path(sys.argv[2])
real_run = subprocess.run
def fake_run(cmd, **kw):                    # apptainer exec ... --bind HOST:/work ... SIF bash -c SCRIPT -> run SCRIPT in HOST
    assert cmd[0] == "apptainer", cmd
    return real_run(["bash", "-c", cmd[-1]], cwd=cmd[cmd.index("--bind") + 1].split(":")[0], **kw)
CR.subprocess = type(sys)("fake_subprocess"); CR.subprocess.run = fake_run
class Env:                                  # RepoEnv.raw without the container
    def __init__(self, host): self.host = host
    def raw(self, command, timeout=600):
        r = real_run(["bash", "-c", command], cwd=self.host, capture_output=True, text=True)
        return {"output": r.stdout + r.stderr, "returncode": r.returncode}
def log(ws): return real_run(["git", "log", "--format=%s"], cwd=ws, capture_output=True, text=True).stdout.split("\n")[:-1]
def files(ws): return sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if ".git" not in p.relative_to(ws).parts and not p.is_dir())
out = {}
seed = T / "p1" / "seed"; (seed / "sub").mkdir(parents=True)
(seed / "README.md").write_text("hi\n"); (seed / "sub" / "a.py").write_text("x = 1\n"); os.symlink("README.md", seed / "link")
ws = T / "p1" / "mem_fusednote" / "repo"
out["copied"] = CR.ensure_git_workspace(ws, "img.sif", "/work", seed)
out["p1_log"], out["p1_files"], out["p1_link"] = log(ws), files(ws), os.readlink(ws / "link")
out["recopy"] = CR.ensure_git_workspace(ws, "img.sif", "/work", seed)              # an existing workspace is left alone
out["p1_log_again"] = log(ws)
ws2 = T / "p2" / "seq" / "repo"
out["empty_copied"] = CR.ensure_git_workspace(ws2, "img.sif", "/work", T / "p2" / "seed")   # no seed: one empty commit
out["p2_log"] = log(ws2)
clone = T / "p3" / "seed"; clone.mkdir(parents=True); (clone / "f.txt").write_text("f\n")
real_run(["git", "init", "-q"], cwd=clone); real_run(["git", "add", "-A"], cwd=clone)
real_run(["git", "-c", "user.name=c", "-c", "user.email=c@c", "commit", "-q", "-m", "upstream commit"], cwd=clone)
ws3 = T / "p3" / "mem_factsall" / "repo"
CR.ensure_git_workspace(ws3, "img.sif", "/work", clone)
out["p3_log"] = log(ws3)
# a task later: the user adds a file -> committed as 'user: add files' before the task base
e = Env(ws)
(ws / "data.csv").write_text("1\n"); (seed / "data.csv").write_text("1\n")
CR.commit_user_files(e); out["after_user"] = log(ws)
CR.commit_user_files(e); out["after_noop"] = log(ws)
# --fresh rerun of task 2: task 1 deleted README.md (a seed file) and added agent.py, task 2 added b.py; the user then
# uploaded late.txt (uncommitted). rewind() resets to the parent of 'rsm: task 2' and cleans: late.txt must come back,
# README.md must not (the history that remains deleted it)
commit = "git add -A && git -c user.name=rsm -c user.email=rsm@local commit -qm"
real_run(["bash", "-c", f"rm README.md && echo a > agent.py && {commit} 'rsm: task 1' && echo b > b.py && {commit} 'rsm: task 2'"], cwd=ws)
(ws / "late.txt").write_text("late\n"); (seed / "late.txt").write_text("late\n")
base = real_run(["git", "rev-parse", "HEAD^"], cwd=ws, capture_output=True, text=True).stdout.strip()
real_run(["bash", "-c", f"git reset -q --hard {base} && git clean -fdq"], cwd=ws)
out["before_restore"] = files(ws)
out["restored"] = CR.restore_user_files(e, ws, seed)
out["after_restore"] = files(ws)
CR.commit_user_files(e); out["after_restore_log"] = log(ws)
print(json.dumps(out))
'''


def test_runner_starts_workspaces_from_the_seed(tmp_path):
    """custom_runner's workspace setup without Apptainer: the container call runs its script on the host instead."""
    probe = tmp_path / "probe.py"
    probe.write_text(RUNNER_PROBE)
    (tmp_path / "mswea").mkdir()
    env = {**GIT_ENV, "MSWEA_GLOBAL_CONFIG_DIR": str(tmp_path / "mswea"), "MSWEA_SILENT_STARTUP": "1", "DEMO_CUSTOM_WORK": str(tmp_path / "work"),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    r = subprocess.run([sys.executable, str(probe), str(ROOT), str(tmp_path / "t")], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o["copied"] is True and o["p1_log"] == ["sandbox: initial files"] and o["p1_files"] == ["README.md", "link", "sub/a.py"]
    assert o["p1_link"] == "README.md"                                              # links are copied as links
    assert o["recopy"] is False and o["p1_log_again"] == ["sandbox: initial files"]
    assert o["empty_copied"] is False and o["p2_log"] == ["sandbox: empty workspace"]
    assert o["p3_log"] == ["upstream commit"]                                        # a cloned seed keeps its own history
    assert o["after_user"] == ["user: add files", "sandbox: initial files"] and o["after_noop"] == o["after_user"]
    assert o["before_restore"] == ["agent.py", "data.csv", "link", "sub/a.py"]
    assert o["restored"] == 1 and o["after_restore"] == ["agent.py", "data.csv", "late.txt", "link", "sub/a.py"]
    assert o["after_restore_log"][:2] == ["user: add files", "rsm: task 1"]


REWIND_PROBE = r'''
import json, pathlib, sys
sys.path.insert(0, sys.argv[1] + "/demo"); sys.path.insert(0, sys.argv[1])
import custom_runner as CR
T = pathlib.Path(sys.argv[2])
class Env:                                  # no repository: rewind() finds no 'rsm: task' commit and leaves the workspace alone
    def raw(self, command, timeout=600): return {"output": "", "returncode": 0}
note = lambda k: [f"p-{k}", f"DID: note of task {k}"]
def arm(name, snaps, rows):
    out = T / name; out.mkdir(parents=True)
    for k, items in snaps.items(): (out / f"mem_store_{k}.json").write_text(json.dumps({"order": k, "nodes": 1, "items": items}))
    (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (out / "store.json").write_text(json.dumps(snaps[max(snaps)] if snaps else [note(9)]))
    return out
res = {}
# task 1 done, task 2 stopped (no snapshot, no results row), task 3 done; rerun task 3 -> the store after task 1
out = arm("stopped", {1: [note(1)], 3: [note(1), note(3)]}, [{"order": 1, "mem_nodes": 1}, {"order": 3, "mem_nodes": 1}])
CR.rewind(Env(), out, 3, out / "store.json", True)
res["stopped"] = [json.load(open(out / "store.json")), sorted(p.name for p in out.glob("mem_store_*")),
                  [json.loads(l)["order"] for l in open(out / "results.jsonl")]]
# task 1 stopped, task 2 done; rerun task 2 -> empty (nothing before it wrote memory)
out = arm("first", {2: [note(2)]}, [{"order": 2, "mem_nodes": 1}])
CR.rewind(Env(), out, 2, out / "store.json", True); res["first"] = json.load(open(out / "store.json"))
# task 2 wrote memory but its snapshot is gone: the state before task 3 is unknown -> the store is kept (warning)
out = arm("lost", {1: [note(1)]}, [{"order": 1, "mem_nodes": 1}, {"order": 2, "mem_nodes": 1}, {"order": 3, "mem_nodes": 1}])
(out / "store.json").write_text(json.dumps([note(1), note(2), note(3)]))
CR.rewind(Env(), out, 3, out / "store.json", True); res["lost"] = json.load(open(out / "store.json"))
print(json.dumps(res))
'''


def test_rerun_after_a_stopped_task_rewinds_memory_to_the_last_snapshot(tmp_path):
    """↻ Rerun of task 3 when task 2 was stopped: there is no mem_store_2.json, and the store must go back to the snapshot
    after task 1 (the stopped task wrote nothing), not keep the replaced attempt's own note."""
    probe = tmp_path / "probe.py"
    probe.write_text(REWIND_PROBE)
    (tmp_path / "mswea").mkdir()
    env = {**GIT_ENV, "MSWEA_GLOBAL_CONFIG_DIR": str(tmp_path / "mswea"), "MSWEA_SILENT_STARTUP": "1", "RSM_RUNS_DIR": str(tmp_path / "runs")}
    r = subprocess.run([sys.executable, str(probe), str(ROOT), str(tmp_path / "t")], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert "memory store reset to 1 item(s) (state after task 1)" in r.stdout
    store, snaps, orders = o["stopped"]
    assert store == [["p-1", "DID: note of task 1"]] and snaps == ["mem_store_1.json"] and orders == [1]
    assert o["first"] == [] and "empty: no earlier task wrote memory" in r.stdout
    assert len(o["lost"]) == 3 and "warning: no memory snapshot of the state before task 3" in r.stdout


# ------------------------------------------------------------------ Files tab: repository tree and file contents

def test_tree_and_file_of_a_chain_image(sandbox_env, monkeypatch):
    """ChainSWE chain: /api/tree lists the image repo's tracked files (cached), /api/file reads one; path and size reach the
    image as arguments only (never inside the bash script), and '..' / absolute / .git / NUL paths are refused before it
    runs. A backslash or a newline is an ordinary character of a file name: it reaches the image as an argument."""
    calls = []

    def fake_run(argv, **kw):
        assert argv[:5] == S._IMG and argv[6:8] == ["bash", "-c"] and argv[9] == "_"
        script, args = argv[8], argv[10:]
        calls.append((script, args))
        assert "pyccel/ast" not in script and "123" not in script        # interpolation would allow shell injection
        if "ls-files" in script:
            return subprocess.CompletedProcess(argv, 0, b"README.md\0pyccel/ast/core.py\0pyccel/ast/class_defs.py\0", b"")
        if args[1] == "pyccel/ast/core.py":
            return subprocess.CompletedProcess(argv, 0, b"class Core:\n    pass\n", b"")
        if args[1] == "bin.dat":
            return subprocess.CompletedProcess(argv, 0, b"\x00\x01\x02", b"")
        if args[1] == "evil":
            return subprocess.CompletedProcess(argv, 3, b"", b"the path leaves the repository\n")
        return subprocess.CompletedProcess(argv, 4, b"", b"not found\n")
    monkeypatch.setattr(S.subprocess, "run", fake_run)
    monkeypatch.setattr(S, "_REPO_DIR", {str(S._sif_path(SHOWCASE)): "/pyccel"})
    monkeypatch.setattr(S, "_TREE_CACHE", collections.OrderedDict())
    t = client.get("/api/tree", params={"cid": SHOWCASE, "run": "v3main100", "arm": "mem_fusednote"}).json()
    assert t["files"] == ["README.md", "pyccel/ast/class_defs.py", "pyccel/ast/core.py"] and t["root"] == "/pyccel" and t["at"] == "base"
    assert t["truncated"] is False and t["n"] == 3 and "base commit" in t["source"] and calls[0][1] == ["/pyccel"]
    client.get("/api/tree", params={"cid": SHOWCASE}); assert len(calls) == 1          # cached per image
    f = client.get("/api/file", params={"cid": SHOWCASE, "path": "pyccel/ast/core.py", "max_bytes": 123}).json()
    assert f["content"].startswith("class Core") and f["root"] == "/pyccel" and f["at"] == "base" and calls[-1][1] == ["/pyccel", "pyccel/ast/core.py", "123"]
    assert client.get("/api/file", params={"cid": SHOWCASE, "path": "./pyccel//ast/core.py"}).json()["path"] == "pyccel/ast/core.py"
    assert client.get("/api/file", params={"cid": SHOWCASE, "path": "bin.dat"}).json()["binary"] is True
    assert client.get("/api/file", params={"cid": SHOWCASE, "path": "evil"}).status_code == 400            # symlink out of the repo
    assert client.get("/api/file", params={"cid": SHOWCASE, "path": "nope.py"}).status_code == 404
    assert client.get("/api/file", params={"cid": SHOWCASE, "path": "odd\\na\nme.py"}).status_code == 404
    assert calls[-1][1] == ["/pyccel", "odd\\na\nme.py", "200000"]
    n = len(calls)
    for bad in ("../etc/passwd", "/etc/passwd", "a/../../b", ".git/config", "a\0b", "", "./", "x" * 5000):
        assert client.get("/api/file", params={"cid": SHOWCASE, "path": bad}).status_code in (400, 422), bad
    assert len(calls) == n                                                               # refused before the image runs
    monkeypatch.setattr(S, "TREE_MAX", 2); S._TREE_CACHE.clear()
    t = client.get("/api/tree", params={"cid": SHOWCASE}).json()
    assert t["truncated"] is True and len(t["files"]) == 2 and t["n"] == 3
    assert client.get("/api/tree", params={"cid": "nope"}).status_code == 404


def test_tree_404_without_an_image():
    other = next(c for c, v in S.CHAINS.items() if not v.get("custom") and not S._sif_ok(c))
    r = client.get("/api/tree", params={"cid": other})
    assert r.status_code == 404 and "image" in r.json()["detail"]


def _commit(ws, msg):
    _git(ws, "add", "-A"); _git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", msg)


def test_tree_and_file_of_a_sandbox_workspace(sandbox_env, monkeypatch):
    """Sandbox project: the arm's workspace right after the session's task (its 'rsm: task N' commit), or the working copy
    now while that commit does not exist (a running task), read without running the workspace's git config; no symlink is
    ever followed (a linked file or folder is listed, not read; a linked folder is not descended)."""
    client.post("/api/custom/project", json={"name": "tp"})
    ws = sandbox_env / "work" / "tp" / "mem_fusednote" / "repo"
    (ws / "src").mkdir(parents=True)
    (ws / "README.md").write_text("v1\n"); (ws / "src" / "app.py").write_text("print(1)\n")
    _git(ws, "init", "-q"); _commit(ws, "rsm: task 1")
    (ws / "README.md").write_text("v2\n"); (ws / "src" / "new.py").write_text("x = 2\n"); os.symlink("/etc/hostname", ws / "src" / "link")
    (ws / "back\\slash.txt").write_text("b\n"); (ws / "new\nline.txt").write_text("n\n")
    _commit(ws, "rsm: task 2")
    marker = sandbox_env / "repo-config-ran"
    _git(ws, "config", "core.fsmonitor", f"touch {marker}"); (ws / ".gitattributes").write_text("* filter=evil\n")
    _git(ws, "config", "filter.evil.smudge", f"touch {marker}; cat")
    (ws / "scratch.txt").write_text("uncommitted\n"); os.symlink("/etc/passwd", ws / "out"); os.symlink(sandbox_env / "work", ws / "lnk")
    q = {"cid": "tp", "run": "custom", "arm": "mem_fusednote"}
    t1 = client.get("/api/tree", params={**q, "order": 1}).json()
    assert t1["files"] == ["README.md", "src/app.py"] and t1["at"] == "task" and "after task 1" in t1["source"] and t1["commit"]
    t2 = client.get("/api/tree", params={**q, "order": 2}).json()
    assert t2["files"] == ["README.md", "back\\slash.txt", "new\nline.txt", "src/app.py", "src/link", "src/new.py"] and t2["at"] == "task"
    w = client.get("/api/tree", params={**q, "order": 3}).json()                                 # no commit for task 3 yet: now
    assert w["at"] == "work" and w["commit"] is None and not w["truncated"] and w["n"] == len(w["files"])
    assert w["files"] == [".gitattributes", "README.md", "back\\slash.txt", "lnk", "new\nline.txt", "out", "scratch.txt", "src/app.py", "src/link", "src/new.py"]
    monkeypatch.setattr(S, "WORK_LIST_MAX", 4); S._WORK_CACHE.clear()                     # a huge working copy: the walk stops, says so
    c = client.get("/api/tree", params={**q, "order": 3}).json()
    assert c["n_partial"] is True and c["truncated"] is True and len(c["files"]) <= 4 and c["n"] <= 4
    monkeypatch.setattr(S, "WORK_LIST_MAX", 200_000); S._WORK_CACHE.clear()
    f = lambda path, **kw: client.get("/api/file", params={**q, "path": path, **kw})
    assert f("README.md", order=1).json()["content"] == "v1\n" and f("README.md", order=1).json()["at"] == "task"
    assert f("README.md", order=3).json()["content"] == "v2\n" and f("README.md").json()["at"] == "work"
    assert f("src/link", order=2).json()["content"] == "(symbolic link to /etc/hostname)"       # the committed link, never followed
    assert f("src/link").status_code == 400 and f("lnk/tp/mem_fusednote/repo/README.md").status_code == 400
    assert f("back\\slash.txt", order=2).json()["content"] == "b\n" and f("new\nline.txt", order=2).json()["content"] == "n\n"
    w = f("scratch.txt", order=3).json(); assert w["content"] == "uncommitted\n" and w["at"] == "work" and w["in_task"] is None
    w = f("scratch.txt", order=2).json(); assert w["at"] == "work" and w["in_task"] is False and "not in the commit" in w["source"]
    assert f("out").status_code == 400 and f("nope.txt").status_code == 404 and f("src").status_code == 404
    for bad in ("../tp/seed/x", "/etc/passwd", ".git/config", "src/../../x", "a\0b"):
        assert f(bad).status_code == 400, bad
    assert not marker.exists()                                                                   # the agent's git config never ran
    r = client.get("/api/tree", params={**q, "arm": "seq"}); assert r.status_code == 404 and "has not run" in r.json()["detail"]
    assert client.get("/api/tree", params={**q, "arm": "../x"}).status_code == 400
    assert client.get("/api/tree", params={"cid": "tp"}).status_code == 400                     # sandbox needs the arm
    assert not [p for p in (sandbox_env / "work" / ".incoming").iterdir() if p.name.startswith("tree-")]


_SWAP = """import ctypes, os, sys
libc = ctypes.CDLL(None, use_errno=True); a, b = os.fsencode(sys.argv[1]), os.fsencode(sys.argv[2])
libc.renameat2(-100, a, -100, b, 2); print("go", flush=True)
while True:
    libc.renameat2(-100, a, -100, b, 2)       # AT_FDCWD, RENAME_EXCHANGE: the folder and the symlink trade places atomically
"""


def test_a_folder_swapped_for_a_symlink_never_redirects_a_read(sandbox_env):
    """The agent can run a background loop that swaps a folder of the workspace with a symlink to a folder outside it
    (its target resolves on the host, e.g. /proc/self): a read goes through folder descriptors opened with O_NOFOLLOW, so
    it returns the workspace file or an error, never the outside file."""
    client.post("/api/custom/project", json={"name": "race"})
    ws = sandbox_env / "work" / "race" / "mem_fusednote" / "repo"; (ws / "sub").mkdir(parents=True)
    outside = sandbox_env / "outside"; outside.mkdir(); (outside / "secret.txt").write_text("OUTSIDE\n")
    (ws / "sub" / "secret.txt").write_text("decoy\n"); os.symlink(outside, ws / "alt")
    q = {"cid": "race", "run": "custom", "arm": "mem_fusednote", "path": "alt/secret.txt"}
    assert client.get("/api/file", params=q).status_code == 400
    sw = subprocess.Popen([sys.executable, "-c", _SWAP, str(ws / "sub"), str(ws / "alt")], stdout=subprocess.PIPE, text=True)
    seen = collections.Counter()
    try:
        assert sw.stdout.readline().strip() == "go"
        t0 = time.time()
        while time.time() - t0 < 1.5:
            try:
                seen[S._read_beneath(ws, "sub/secret.txt", 100)] += 1
            except S._NotServed as e:
                seen[e.status] += 1
        for _ in range(60):
            r = client.get("/api/file", params={**q, "path": "sub/secret.txt"})
            seen["api " + (r.json().get("content") or str(r.status_code)).strip()] += 1
    finally:
        sw.kill(); sw.wait()
    assert b"OUTSIDE\n" not in seen and "api OUTSIDE" not in seen and seen[b"decoy\n"] > 0, seen


def test_git_files_the_agent_plants_never_hang_the_server(sandbox_env, monkeypatch):
    """HEAD or a ref made a FIFO or a link to /dev/zero: read non-blocking, size-capped, regular files only, so the tree
    falls back to the working copy at once and the zip download answers. A git object made a FIFO: git is killed after
    GIT_READ_SECS (504); requests behind it for the same workspace wait WS_WAIT_SECS, then get 503."""
    monkeypatch.setattr(S, "GIT_READ_SECS", 1.5); monkeypatch.setattr(S, "WS_WAIT_SECS", 0.3)
    monkeypatch.setattr(S, "_TREE_CACHE", collections.OrderedDict()); monkeypatch.setattr(S, "_TASK_COMMIT", collections.OrderedDict())
    client.post("/api/custom/project", json={"name": "trap"})
    ws = sandbox_env / "work" / "trap" / "mem_fusednote" / "repo"; ws.mkdir(parents=True)
    (ws / "a.txt").write_text("hello\n"); _git(ws, "init", "-q"); _commit(ws, "rsm: task 1")
    q = {"cid": "trap", "run": "custom", "arm": "mem_fusednote", "order": 1}
    assert client.get("/api/tree", params=q).json()["at"] == "task"

    def within(secs, fn):
        out = []
        th = threading.Thread(target=lambda: out.append(fn()), daemon=True); th.start(); th.join(secs)
        assert not th.is_alive(), "the request hung"
        return out[0]
    gd = ws / ".git"
    ref = gd / (gd / "HEAD").read_text().split(":", 1)[1].strip(); sha = ref.read_text()
    for target, plant in ((ref, "fifo"), (ref, "zero"), (gd / "HEAD", "zero")):
        saved = target.read_text(); target.unlink()
        os.mkfifo(target) if plant == "fifo" else os.symlink("/dev/zero", target)
        S._TASK_COMMIT.clear()
        r = within(10, lambda: client.get("/api/tree", params=q))
        assert r.status_code == 200 and r.json()["at"] == "work" and r.json()["files"] == ["a.txt"], (plant, r.text[:200])
        assert within(10, lambda: client.get("/api/file", params={**q, "path": "a.txt"})).json()["content"] == "hello\n"
        assert within(10, lambda: client.get("/api/custom/project/trap/download", params={"kind": "kv"})).status_code == 404
        target.unlink(); target.write_text(saved)
    assert ref.read_text() == sha and client.get("/api/tree", params=q).json()["at"] == "task"
    blob = _git(ws, "rev-parse", "HEAD:a.txt").strip(); obj = gd / "objects" / blob[:2] / blob[2:]
    data = obj.read_bytes(); obj.chmod(0o644); obj.unlink(); os.mkfifo(obj)
    codes = []
    ths = [threading.Thread(target=lambda: codes.append(client.get("/api/file", params={**q, "path": "a.txt"}).status_code), daemon=True) for _ in range(3)]
    t0 = time.time()
    for th in ths:
        th.start()
    for th in ths:
        th.join(10)
    assert sorted(codes) == [503, 503, 504] and time.time() - t0 < 6, codes
    obj.unlink(); obj.write_bytes(data)
    assert client.get("/api/file", params={**q, "path": "a.txt"}).json()["content"] == "hello\n"


def test_a_session_whose_workspace_is_gone(sandbox_env, monkeypatch):
    """A memory system ran in a project but its workspace is no longer on the server (tablib-play on the demo): a project
    started from an example repo shows that repo's image instead (the repo before any task, said so); any other project
    says that the workspace is gone — or, for a memory system that never ran, that it has not run yet."""
    tag = S.CHAINS[SHOWCASE]["tag"]
    assert client.post("/api/custom/project", json={"name": "ex", "source": "image", "sandbox": tag}).status_code == 200
    assert client.post("/api/custom/project", json={"name": "plain"}).status_code == 200
    runs = sandbox_env / "runs"
    for d in ("ex__s0__mem_fusednote", "plain__s0__mem_fusednote"):
        (runs / "custom" / d).mkdir(parents=True)
    monkeypatch.setattr(S, "RUNS", runs)
    real_run = subprocess.run

    def fake_run(argv, **kw):
        if argv[:5] != S._IMG:
            return real_run(argv, **kw)
        script, args = argv[8], argv[10:]
        if "ls-files" in script:
            return subprocess.CompletedProcess(argv, 0, b"README.md\0src/a.py\0", b"")
        if args[1:2] == ["src/a.py"]:
            return subprocess.CompletedProcess(argv, 0, b"A = 1\n", b"")
        return subprocess.CompletedProcess(argv, 4, b"", b"not found\n")
    monkeypatch.setattr(S.subprocess, "run", fake_run)
    monkeypatch.setattr(S, "_REPO_DIR", {str(S._sif_path("ex")): "/repo"})
    monkeypatch.setattr(S, "_TREE_CACHE", collections.OrderedDict())
    q = {"cid": "ex", "run": "custom", "arm": "mem_fusednote", "order": 1}
    t = client.get("/api/tree", params=q).json()
    assert t["files"] == ["README.md", "src/a.py"] and t["at"] == "base" and "no longer on the server" in t["gone"] and "before any task" in t["source"]
    f = client.get("/api/file", params={**q, "path": "src/a.py"}).json()
    assert f["content"] == "A = 1\n" and f["at"] == "base" and "no longer on the server" in f["gone"] and f["root"] == "/repo"
    assert client.get("/api/file", params={**q, "path": "made_by_agent.py"}).status_code == 404
    r = client.get("/api/tree", params={**q, "cid": "plain"}); assert r.status_code == 404 and "no longer on the server" in r.json()["detail"]
    r = client.get("/api/file", params={**q, "cid": "plain", "path": "x.py"}); assert r.status_code == 404 and "no longer on the server" in r.json()["detail"]
    r = client.get("/api/tree", params={**q, "cid": "plain", "arm": "seq"}); assert r.status_code == 404 and "has not run" in r.json()["detail"]


def test_mentioned_files_leave_out_urls_and_path_fragments():
    """URLs and the tails of absolute or dotted paths are not files of the repository: they must not become folders of the
    Files tree ("docs.pytest.org/…", "10/inspect.py", "packages/x.py"); a hidden file keeps its dot. An absolute path under
    the repository counts as its relative path, in a command too; a relative path that starts with the repository's folder
    name ("pyccel/ast/core.py" in /pyccel) is kept whole."""
    out = ("see https://docs.pytest.org/en/stable/how-to/capture-warnings.html and https://github.com/o/r/blob/main/tutorial/x.md\n"
           '  File "/usr/lib/python3.10/inspect.py", line 1\n/usr/local/lib/python3.11/site-packages/pkg/mod.py:3\n'
           './src/b.py\n.pre-commit-config.yaml\nsetup.cfg\nmail me@host.org/notes.md\n  File "/pyccel/tests/t.py", line 2\n/usr/pyccel/lib.py\n'
           "---pytest.ini---\nmoto/dyna\n...[output truncated]...\no/dynamodb/x.py:3: tail\ndocs/ok.md\n@pytest.mark.c\nsrc/lib.c")
    turns = [{"step": 1, "command": "cat pyccel/ast/core.py && cat /pyccel/README.md /pyccel/.github/x.yml && cd pyccel && cat ./util.py",
              "observation": {"output": out, "returncode": 0}}]
    files = {f["path"]: f for f in S._mentioned_files(turns, "/pyccel", set())}
    assert {"pyccel/ast/core.py", "README.md", ".github/x.yml", "util.py", "src/b.py", ".pre-commit-config.yaml", "setup.cfg", "tests/t.py"} <= set(files), list(files)
    assert files["README.md"]["in_commands"] == 1 and files["pyccel/ast/core.py"]["in_commands"] == 1 and files["tests/t.py"]["in_commands"] == 0
    assert not [p for p in files if "pytest.org" in p or "github.com" in p or "tutorial" in p or "inspect.py" in p or "mod.py" in p or "notes.md" in p or "lib.py" in p or p.startswith(("ast/", "o/", "-"))], list(files)
    assert "docs/ok.md" in files and "src/lib.c" in files and "pytest.mark.c" not in files   # the line after the cut one counts; a marker is no file


# ------------------------------------------------------------------ MemOp live runs (round 2)

def test_memop_is_a_live_kind_in_page_order():
    m = client.get("/api/meta").json()
    assert m["live_kinds"] == ["kv", "mem0", "memop", "none"] and set(m["live_kinds"]) == set(S.LIVE_SYSTEMS)
    assert m["live_systems"]["memop"] == ["memop"] and S.LIVE_SYSTEMS["memop"] == "memop" and S.ALL_ARMS["memop"] == "mem_memop"
    assert "rawcat" not in m["live_kinds"] and m["arms"] == {"none": "seq", "mem0": "mem_factsall", "kv": "mem_fusednote"}   # agent view unchanged


def test_memop_live_start_builds_the_harness_command(monkeypatch, tmp_path):
    """kind memop passes the kind check (no image in the tests: refused for the SIF, not for the kind); with an image and a
    local endpoint, _start_live runs harness/mini_swe_agent.py --memory-system memop into <run>/<cid>__s0__mem_memop
    with the ledger on. Full history stays recorded only, in plain words, on both routes."""
    r = client.post("/api/live/start", json={"cid": SHOWCASE, "kind": "memop", "provider": "local"})
    assert r.status_code == 400 and "SIF missing" in r.json()["detail"]
    r = client.post("/api/live/start", json={"cid": SHOWCASE, "kind": "rawcat", "provider": "local"})
    assert r.status_code == 400 and "memop (MemOp)" in r.json()["detail"] and "Full history can't run live" in r.json()["detail"]
    r = client.post("/api/configs/run", json={"cid": SHOWCASE, "configs": [{"kind": "rawcat", "provider": "local", "model": "m"}, {"provider": "local", "model": "m"}]}).json()
    assert r["jobs"] == [] and len(r["errors"]) == 2 and all("can't run live" in e for e in r["errors"])
    popen = []

    class FakeProc:
        pid = 4242
        def wait(self):
            return 0
    monkeypatch.setattr(S, "_sif_ok", lambda cid: True)
    monkeypatch.setattr(S, "_endpoint_ok", lambda base: True)
    monkeypatch.setattr(S, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(S, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(S.subprocess, "Popen", lambda cmd, **kw: (popen.append((cmd, kw["env"])), FakeProc())[1])
    try:
        j = S._start_live(S.LiveReq(cid=SHOWCASE, kind="memop", provider="local", api_base="http://127.0.0.1:1/v1", model="coder-next", run_id="r2memop"))
        cmd, env = popen[0]
        assert j["kind"] == "memop" and j["arm"] == "mem_memop" and j["run_id"] == "r2memop"
        assert cmd[cmd.index("--memory-system") + 1] == "memop" and "--arm" not in cmd and cmd[cmd.index("--run-id") + 1] == "r2memop"
        assert env["RSM_LEDGER"] == "1" and env["RSM_MEM_SAVE"] == "1" and env["RSM_API_BASE"] == "http://127.0.0.1:1/v1" and env["RSM_WRITER_MODEL"] == "coder-next"
        assert "RSM_OPENAI_STRICT" not in env                   # local vLLM: the paper's request body
        with pytest.raises(S.HTTPException) as e:
            S._start_live(S.LiveReq(cid=SHOWCASE, kind="memop", provider="local", memory_system="factsall"))
        assert e.value.status_code == 400 and "unknown memory system factsall for memop" in e.value.detail
        assert S._start_live(S.LiveReq(cid=SHOWCASE, kind="memop", provider="local", memory_system="memop", run_id="r2b"))["arm"] == "mem_memop"
    finally:
        mine = [k for k, v in S.JOBS.items() if v.get("run_id") in ("r2memop", "r2b")]
        for _ in range(100):                               # let the jobs' _wait threads record the end first
            if all(S.JOBS[k]["status"] != "running" for k in mine):
                break
            time.sleep(0.02)
        for k in mine:
            S.JOBS.pop(k, None)


def test_memop_duplicate_guard_and_job_records(monkeypatch):
    """Compare's runLive posts inline {kind, provider, model} configs to /api/configs/run: a memop column starts a job on
    the cfg__ run of its model, and a second MemOp run of the same chain and model is refused (409 / errors[])."""
    started = []

    def fake_start(req):                                   # as tests/ui/replay_server.py: register a running job
        if req.kind not in S.LIVE_SYSTEMS:
            raise S.HTTPException(400, S._live_kind_error(req.kind))
        job = f"r2mo{len(started)}"
        S.JOBS[job] = {"id": job, "cid": req.cid, "kind": req.kind, "arm": S.ALL_ARMS[req.kind], "run_id": req.run_id or "live_x",
                       "started": time.time(), "status": "running", "provider": req.provider, "model": S._live_model(req), "pid": 0}
        started.append(job)
        return S.JOBS[job]
    monkeypatch.setattr(S, "_start_live", fake_start)
    try:
        r = client.post("/api/configs/run", json={"cid": SHOWCASE, "configs": [{"kind": "memop", "provider": "local", "model": "m-mo"},
                                                                              {"kind": "kv", "provider": "local", "model": "m-mo"}]}).json()
        assert r["errors"] == [] and [(j["kind"], j["arm"], j["run_id"]) for j in r["jobs"]] == [
            ("memop", "mem_memop", S.config_run_id("local", "m-mo")), ("kv", "mem_fusednote", S.config_run_id("local", "m-mo"))]
        assert r["jobs"][0]["config"] == "memop+m-mo"
        r2 = client.post("/api/live/start", json={"cid": SHOWCASE, "kind": "memop", "provider": "local", "model": "m-mo"})
        assert r2.status_code == 409 and r2.json()["detail"] == "A live run of this chain with MemOp (m-mo) is already running — watch it or stop it first"
        r3 = client.post("/api/configs/run", json={"cid": SHOWCASE, "configs": [{"kind": "memop", "provider": "local", "model": "m-mo"}]}).json()
        assert r3["jobs"] == [] and r3["errors"] == [r2.json()["detail"]] and len(started) == 2
        assert client.post("/api/live/start", json={"cid": SHOWCASE, "kind": "memop", "provider": "local", "model": "m-other"}).status_code == 200
        assert [j["kind"] for j in client.get("/api/live").json() if j["id"] in started].count("memop") == 2
    finally:
        for j in started:
            S.JOBS.pop(j, None)


def _capture_writer(monkeypatch):
    import memory.baselines.memop_prompt as MO
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append({"url": req.full_url, "data": req.data, "headers": {k.lower(): v for k, v in req.header_items()}})
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "<snippet>## Task Guidance\n### Repo\n- You run the tests first.</snippet>"}}]}).encode())
    monkeypatch.setattr(MO.urllib.request, "urlopen", fake_urlopen)
    for k in ("RSM_OPENAI_STRICT", "RSM_API_KEY", "RSM_WRITER_API_KEY", "RSM_WRITER_API", "RSM_WRITER_MODEL", "RSM_WRITER_MAXTOK"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RSM_API_BASE", "http://127.0.0.1:1/v1")
    return MO, seen


def test_memop_writer_request_local_body_is_unchanged(monkeypatch):
    """Local vLLM (no RSM_OPENAI_STRICT): the exact bytes the recorded paper runs sent — key order, temperature 0,
    max_tokens, chat_template_kwargs — and no Authorization header (none for an EMPTY key either)."""
    MO, seen = _capture_writer(monkeypatch)
    assert MO.distill_memop_guidance("old guidance", "ASSISTANT:\nls") == "### Repo\n- You run the tests first."
    sysp = (MO.PROMPTS / "memop_memory_prompt.txt").read_text()
    user = "Current task guidance from previous sessions:\n<snippet>old guidance</snippet>\n\nNew coding agent trajectory:\nASSISTANT:\nls"
    want = json.dumps({"model": "coder-next", "temperature": 0.0, "max_tokens": 800, "chat_template_kwargs": {"enable_thinking": False},
                       "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": user}]}).encode()
    assert seen[0]["data"] == want and seen[0]["url"] == "http://127.0.0.1:1/v1/chat/completions"
    assert seen[0]["headers"] == {"content-type": "application/json"}
    monkeypatch.setenv("RSM_API_KEY", "EMPTY")
    MO.distill_memop_guidance("old guidance", "ASSISTANT:\nls")
    assert seen[1]["data"] == want and "authorization" not in seen[1]["headers"]


def test_memop_writer_request_strict_for_openai_gateways(monkeypatch):
    """RSM_OPENAI_STRICT=1 (GoodVision / Azure): no temperature, no chat_template_kwargs, max_completion_tokens instead of
    max_tokens, and Bearer auth from RSM_WRITER_API_KEY, else RSM_API_KEY, unless unset or EMPTY."""
    MO, seen = _capture_writer(monkeypatch)
    monkeypatch.setenv("RSM_OPENAI_STRICT", "1")
    monkeypatch.setenv("RSM_API_KEY", "k-actor")
    monkeypatch.setenv("RSM_WRITER_MODEL", "gpt-5.5")
    assert MO.distill_memop_guidance("", "ASSISTANT:\nls") == "### Repo\n- You run the tests first."
    b = json.loads(seen[0]["data"])
    assert set(b) == {"model", "max_completion_tokens", "messages"} and b["max_completion_tokens"] == 800 and b["model"] == "gpt-5.5"
    assert seen[0]["headers"]["authorization"] == "Bearer k-actor"
    monkeypatch.setenv("RSM_WRITER_API_KEY", "k-writer")
    MO.distill_memop_guidance("", "x")
    assert seen[1]["headers"]["authorization"] == "Bearer k-writer"
    monkeypatch.setenv("RSM_WRITER_API_KEY", "EMPTY"); monkeypatch.setenv("RSM_API_KEY", "EMPTY")
    MO.distill_memop_guidance("", "x")
    assert "authorization" not in seen[2]["headers"] and "temperature" not in json.loads(seen[2]["data"])


def test_memop_guidance_lives_in_the_store(tmp_path):
    """The guidance is the store's one item, so the live runs' snapshots (mem_store_<k>.json) and the sandbox runner's
    store.json keep it: a layer restored from the items injects the same guidance."""
    import harness.mem_snapshot as MS
    from memory import base as MB
    import memory.baselines.memop_prompt  # noqa: F401  registers memop
    layer = MB.REGISTRY["memop"]()
    assert layer.store.items == [] and layer.wrap("Q", None).text == "Q"
    layer.guidance = "### Repo\n- You run the tests first."
    assert layer.store.items == [("guidance", "### Repo\n- You run the tests first.")] and layer.stats()["store_chars"] == 35
    MS.save(tmp_path, 1, layer, 1, force=True)
    saved = json.load(open(tmp_path / "mem_store_1.json"))["items"]
    again = MB.REGISTRY["memop"]()
    again.store.items = [tuple(x) for x in saved]
    w = again.wrap("Q", None)
    assert w.injected and "## Task Guidance\n### Repo\n- You run the tests first.\n</memory_context>\n\nQ" in w.text and w.text == layer.wrap("Q", None).text


def test_memop_live_session_cost_and_ledger(monkeypatch, tmp_path):
    """A MemOp live run's ledger rows (calls.jsonl: actor + note_writer) price its sessions, as for the other kinds; the
    recorded mem_memop runs keep their writer estimate."""
    arm = "mem_memop"; src = ROOT / "runs" / "v3main100" / f"{SHOWCASE}__s0__{arm}"
    import shutil
    out = tmp_path / "cfg__local-m" / f"{SHOWCASE}__s0__{arm}"; shutil.copytree(src, out)
    rows = []
    for k in (1, 2, 3):
        rows += [{"slot": k, "call_idx": 1, "role": "note_writer", "model": "coder-next", "prompt_tokens": 1300, "cached_tokens": 0, "uncached_prompt_tokens": 1300, "completion_tokens": 51},
                 {"slot": k, "call_idx": 2, "role": "actor", "model": "coder-next", "prompt_tokens": 2000, "cached_tokens": 0, "uncached_prompt_tokens": 2000, "completion_tokens": 40}]
    (out / "calls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(S, "RUNS", tmp_path)
    os.symlink(ROOT / "runs" / "v3main100", tmp_path / "v3main100")
    a = client.get("/api/runchains", params={"run": "cfg__local-m", "cid": SHOWCASE, "kinds": "kv,mem0,memop,none"}).json()["chains"][0]["arms"]
    assert list(a) == ["memop"] and a["memop"]["arm"] == arm and a["memop"]["usd"] > 0 and all(s["tokens"] == 3391 for s in a["memop"]["sessions"])
    s = client.get("/api/session", params={"run": "cfg__local-m", "arm": arm, "cid": SHOWCASE, "order": 2}).json()
    assert s["kind"] == "memop" and s["tok"]["src"] == s["tok"]["writer_src"] == "ledger" and (s["tok"]["writer_prompt"], s["tok"]["writer_completion"]) == (1300, 51)
    assert [x["role"] for x in s["ledger"]] == ["note_writer", "actor"]
    rec = client.get("/api/session", params={"run": "v3main100", "arm": arm, "cid": SHOWCASE, "order": 2}).json()
    assert rec["tok"]["writer_src"] == "estimate" and rec["tok"]["writer_prompt"] > 0


def test_runchains_arms_say_when_they_started(monkeypatch, tmp_path):
    """Every arm in /api/runchains carries "started" = the earliest mtime of its run directory's files (as /api/liveruns),
    the directory's own mtime when it is empty; Compare dates and sorts the saved cfg__ runs by it."""
    import shutil
    arm = S.ARMS["kv"]; out = tmp_path / "cfg__local-m" / f"{SHOWCASE}__s0__{arm}"
    shutil.copytree(ROOT / "runs" / "v3main100" / f"{SHOWCASE}__s0__{arm}", out)
    for i, p in enumerate(sorted(out.iterdir())):
        os.utime(p, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))
    monkeypatch.setattr(S, "RUNS", tmp_path)
    a = client.get("/api/runchains", params={"run": "cfg__local-m", "cid": SHOWCASE}).json()["chains"][0]["arms"]["kv"]
    assert a["started"] == 1_700_000_000
    empty = tmp_path / "e"; empty.mkdir(); os.utime(empty, (1_600_000_000, 1_600_000_000))
    assert S._dir_started(empty) == 1_600_000_000 and S._dir_started(tmp_path / "gone") == 0


# ------------------------------------------------------------------ task summaries (round 2)

def test_clean_line_rules():
    c = S._clean_line
    assert c("[BUG] Fix #123: crash in `foo.bar()` when x is None.") == "crash in foo.bar() when x is None"
    assert c("### [ENH] Add `pivot` support...") == "Add pivot support"
    assert c("#4512 Something odd") == "Something odd" and c("Fixes #45 - wrong output.") == "wrong output"
    assert c("**Describe the bug**") == "Describe the bug" and c("[tag]") == "" and c("") == ""
    long = "Transpile from DuckDB to BigQuery for UUID results in a BigQuery column type of STRING not UUID"
    assert c(long) == "Transpile from DuckDB to BigQuery for UUID results in a BigQuery column type of…" and len(c(long)) <= 81
    assert c("x" * 100) == "x" * 80 + "…"                                           # no space to cut at
    assert S._issue_summary("\n[Bug]\n\n### `Model.save` drops the id.\nmore") == "Model.save drops the id"
    assert S._prompt_summary("Add a README.md that explains the project. Then write tests.\nmore") == "Add a README.md that explains the project"
    assert S._prompt_summary("Use a library, e.g. requests, to fetch it! Then stop") == "Use a library, e.g. requests, to fetch it!"
    assert S._prompt_summary("\n\n# Make `hello.py` print hi") == "Make hello.py print hi"
    # list markers go; a first piece of fewer than 3 words takes the next sentence too; "E.g." does not end a sentence
    assert S._prompt_summary("1. Create a list of the best restaurants in New Haven\n2. Then rank them") == "Create a list of the best restaurants in New Haven"
    assert S._prompt_summary("Step 1. Add a README.") == "Add a README"
    assert S._prompt_summary("- Add a README") == "Add a README" and S._prompt_summary("* fix the tests. Then push") == "fix the tests"
    assert S._prompt_summary("v2. Update the parser so it accepts tabs") == "v2. Update the parser so it accepts tabs"
    assert S._prompt_summary("E.g. make it faster") == "E.g. make it faster"


def test_zip_members_that_clash_are_refused_before_anything_is_written(sandbox_env):
    """A zip holding a file "a" and a file "a/b" can't be extracted: refused up front, nothing half written."""
    client.post("/api/custom/project", json={"source": "upload", "name": "zc"})
    seed = sandbox_env / "work" / "zc" / "seed"
    r = _upload("zc", "z.zip", _zip([("a", "file"), ("a/b", "x"), ("c.txt", "c")]), unzip=1)
    assert r.status_code == 400 and "nothing was extracted" in r.json()["detail"], r.text
    assert not (seed / "a").exists() and not (seed / "c.txt").exists()
    assert _upload("zc", "ok.zip", _zip([("a/b", "x"), ("c.txt", "c")]), unzip=1).status_code == 200 and (seed / "a" / "b").exists()


def test_memop_memory_view_names_the_guidance_by_its_task(monkeypatch, tmp_path):
    """A live MemOp run saves its guidance as the store item "guidance" and injects it as "## Task Guidance\\n…": the memory view
    must see one item, written by task 1 and shown to task 2 (not an unattributed "memory" item)."""
    g1 = "- Run the tests with pytest -q before submitting"
    tok = {"writer_prompt": 10, "writer_completion": 5, "writer_src": "ledger", "usd": 0.02, "usd_actor": 0.01}
    sess = lambda k, mem: {"order": k, "instance": f"i-{k}", "resolved": True, "memory_text": mem, "injected_chars": len(mem),
                           "injected": bool(mem), "mem_nodes": 1, "mem_write_secs": 1, "mem_write_error": None, "tok": dict(tok)}
    x = {"dir": str(tmp_path), "kind": "memop", "sessions": [sess(1, ""), sess(2, "## Task Guidance\n" + g1)]}
    monkeypatch.setattr(S, "load_chain_arm", lambda *a, **k: x)
    (tmp_path / "mem_store_1.json").write_text(json.dumps({"items": [["guidance", g1]]}))
    m = S.memory_timeline("r", "mem_memop", SHOWCASE, "memop")
    assert m["tasks"][1]["retrieved"]["items"] == [{"name": "guidance", "text": g1, "order": 1}]
    assert m["tasks"][0]["written"]["items"] == [{"name": "guidance", "text": g1, "updated": False}]


def test_task_summaries_load_reload_and_fallback(monkeypatch, tmp_path):
    """demo/task_summaries.json gives every ChainSWE task its summary; DEMO_TASK_SUMMARIES (SUMMARIES_FILE) overrides the
    path; a changed mtime is picked up without a restart; a task without one falls back to its cleaned issue line; a file
    that does not parse keeps the previous summaries."""
    t1 = S.CHAINS[SHOWCASE]["tasks"][0]
    assert t1["summary"] == "Support set.clear() in translated Python code"                     # the shipped file
    assert S.CHAINS["tobymao_sqlglot_20240910_b10255eb"]["tasks"][0]["summary"] == "Fix Snowflake PIVOT queries with a dynamic column list"
    assert all(t.get("summary") for c in S.CHAINS.values() if not c.get("custom") for t in c["tasks"])
    f = tmp_path / "sums.json"
    f.write_text(json.dumps({SHOWCASE: {"1": "  First   task here ", "2": ""}, "nope": {"1": "x"}}))
    monkeypatch.setattr(S, "SUMMARIES_FILE", f)
    try:
        S._refresh_summaries(force=True)
        ts = S.CHAINS[SHOWCASE]["tasks"]
        assert ts[0]["summary"] == "First task here"
        assert ts[1]["summary"] == S._issue_summary(ts[1]["problem_statement"]) == "extend: adds each element of an iterable to the end of the list"
        assert ts[0]["title"] == ts[0]["problem_statement"].strip().splitlines()[0][:160]       # title / problem_statement untouched
        f.write_text(json.dumps({SHOWCASE: {"1": "Edited while running"}}))
        os.utime(f, (time.time() + 5, time.time() + 5))
        S._SUM["checked"] = 0                                                                   # skip the once-a-second throttle
        ch = client.get(f"/api/chain/{SHOWCASE}", params={"run": "v3main100"}).json()
        assert ch["chain"]["tasks"][0]["summary"] == "Edited while running"
        f.write_text("{half written")
        os.utime(f, (time.time() + 10, time.time() + 10))
        S._refresh_summaries(force=True)
        assert S.CHAINS[SHOWCASE]["tasks"][0]["summary"] == "Edited while running"             # kept
        f.unlink()
        S._refresh_summaries(force=True)
        assert S.CHAINS[SHOWCASE]["tasks"][0]["summary"] == S._issue_summary(S.CHAINS[SHOWCASE]["tasks"][0]["problem_statement"])
    finally:
        monkeypatch.undo()
        S._refresh_summaries(force=True)
    assert S.CHAINS[SHOWCASE]["tasks"][0]["summary"] == "Support set.clear() in translated Python code"


def test_summary_next_to_title_everywhere_tasks_are_returned():
    want = {o: S._task_summary(SHOWCASE, o) for o in (1, 2, 3)}
    assert want[1] == "Support set.clear() in translated Python code" and all(want.values())
    ch = client.get(f"/api/chain/{SHOWCASE}", params={"run": "v3main100"}).json()
    assert [t["summary"] for t in ch["chain"]["tasks"]] == [want[1], want[2], want[3]] and all(t["title"] and t["problem_statement"] for t in ch["chain"]["tasks"])
    assert all(s["summary"] == want[s["order"]] for a in ch["arms"].values() for s in a["sessions"])
    assert "summary" not in S.load_chain_arm("v3main100", "mem_fusednote", SHOWCASE)["sessions"][0]          # the cache is not touched
    s = client.get("/api/session", params={"run": "v3main100", "arm": "mem_fusednote", "cid": SHOWCASE, "order": 2}).json()
    assert s["summary"] == want[2] and s["title"] == S.CHAINS[SHOWCASE]["tasks"][1]["title"]
    rc = client.get("/api/runchains", params={"run": "v3main100", "cid": SHOWCASE, "kinds": "kv,mem0,memop,rawcat,none"}).json()
    assert all(x["summary"] == want[x["order"]] for a in rc["chains"][0]["arms"].values() for x in a["sessions"])
    sc = client.get("/api/showcase").json()
    md = next(c for c in sc["chains"] if c["cid"] == DEFAULT_CID)
    assert [(t["order"], t["summary"]) for t in md["tasks"]] == [(o, S._task_summary(DEFAULT_CID, o)) for o in (1, 2, 3)] and all(t["title"] for t in md["tasks"])
    assert all(t["summary"] for c in sc["chains"] for t in c["tasks"]) and all(len(c["tasks"]) == c["n_tasks"] for c in sc["chains"])
    m = client.get(f"/api/memory?run=v3main100&arm=mem_fusednote&cid={SHOWCASE}").json()
    assert [t["summary"] for t in m["tasks"]] == [want[1], want[2], want[3]]


def test_sandbox_task_summary_is_the_prompts_first_sentence(sandbox_env, monkeypatch):
    assert client.post("/api/custom/project", json={"name": "sumproj"}).status_code == 200
    with S._PROJECTS_LOCK:
        ps = S._projects()
        ps["sumproj"]["tasks"] = [{"order": 1, "instance": "sumproj-1-a", "problem_statement": "Add a `hello.py` that prints hi. Then add a test.\nMore text."},
                                  {"order": 2, "instance": "sumproj-2-b", "problem_statement": "[urgent] Fix #3: the test fails on Windows"}]
        S._save_projects(ps); S._register_custom_chains()
    p = next(x for x in client.get("/api/custom/projects").json() if x["name"] == "sumproj")
    assert [(t["title"], t["summary"]) for t in p["tasks"]] == [("Add a `hello.py` that prints hi. Then add a test.", "Add a hello.py that prints hi"),
                                                                ("[urgent] Fix #3: the test fails on Windows", "the test fails on Windows")]
    assert [t["summary"] for t in S.CHAINS["sumproj"]["tasks"]] == ["Add a hello.py that prints hi", "the test fails on Windows"]
    assert S._task_summary("sumproj", 2) == "the test fails on Windows" and S._task_summary("sumproj", 9) == "" and S._task_summary("nope", 1) == ""
    S._refresh_summaries(force=True)                                                            # the file never touches sandbox tasks
    assert S.CHAINS["sumproj"]["tasks"][0]["summary"] == "Add a hello.py that prints hi"
    r = client.post("/api/custom/start", json={"project": "sumproj", "task": "t", "kinds": ["rawcat"]})
    assert r.status_code == 400 and "kv, mem0, none, memop" in r.json()["detail"]
