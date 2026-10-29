"""Unit tests for the demo backend (demo/server.py, demo/providers.py, demo/deploy/check_goodvision.py).

Offline: the network is monkeypatched; the recorded paper run runs/v3main100 (tracked in git) is the fixture.
Run:  pytest -q tests/test_demo.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

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
SHOWCASE = "pyccel_pyccel_20240307"


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
    finally:
        S.JOBS.pop("fakejob1", None); S.JOBS.pop("fakejob2", None)


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
