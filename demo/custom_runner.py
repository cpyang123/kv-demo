"""Run one user-written task in a sandbox with a memory system — the demo's "your own task" mode.

A *project* is a user-made chain: a sandbox image (a ChainSWE repo image at its base commit, or a plain
Python image) plus the tasks typed in the UI, numbered in order. Each memory arm (kv / mem0 / none) keeps
its own persistent workspace and memory store under RUNS/custom/<project>__s0__<arm>/, so task k+1 inherits
the repository state and the notes written after task k — exactly the ChainSWE seq protocol, minus the
grading (there are no F2P/P2P tests for a free-form task: `resolved` is null, `gradable` false).

Everything else is the harness's own machinery: RepoEnv (Apptainer), mini-swe-agent DefaultAgent with the
pinned templates, the registered memory layers, the per-task harness commit, the token ledger (RSM_LEDGER=1).
Output files have the same layout as a chain directory (traj_<k>_*.json, results.jsonl, calls.jsonl,
mem_stats.json), so demo/server.py serves these sessions like any other.

Usage (demo/server.py builds the environment — endpoint, keys, strict switches — exactly as for live chains):
  python demo/custom_runner.py --project NAME --order K --kind kv|mem0|none [--memory-system fusednote]
                               --sandbox <sif basename without .sif> --step-limit 60
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from harness import config as C, constants as K, grading as G          # noqa: E402
from harness.env import RepoEnv                                         # noqa: E402
from harness.mini_swe_agent import ChainRunner, atomic_results          # noqa: E402  (_material, results writer)
from minisweagent.agents.default import DefaultAgent                    # noqa: E402
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel  # noqa: E402
import memory                                                           # noqa: E402  registers the layers
from memory import base as mem_base                                     # noqa: E402

_LEDGER = None
if os.environ.get("RSM_LEDGER") == "1":
    from harness import ledger as _LEDGER                               # noqa: E402

RUNS = pathlib.Path(os.environ.get("RSM_RUNS_DIR", str(ROOT / "runs")))
SIF_DIR = pathlib.Path(os.environ.get("RSM_SIF_DIR", str(ROOT / "sifs")))
WORK = pathlib.Path(os.environ.get("DEMO_CUSTOM_WORK", f"/scratch/{os.environ.get('USER', 'nobody')}/kv/custom"))
PROJECTS = RUNS / "custom" / "projects.json"
SYSTEMS = {"kv": "fusednote", "mem0": "factsall", "none": None}


def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()[:40] or "task"


def load_projects():
    try:
        return json.load(open(PROJECTS))
    except Exception:
        return {}


def sh(env, cmd, timeout=600):
    return env.raw(cmd, timeout=timeout)["output"]


def head(env):
    m = re.findall(r"\b[0-9a-f]{40}\b", sh(env, "git rev-parse HEAD"))
    return m[-1] if m else ""


def ensure_git_workspace(host, sif, repo_path):
    """Generic sandboxes have no repository: make the bound workspace a git repo with one empty commit."""
    host.mkdir(parents=True, exist_ok=True)
    if not (host / ".git").exists():
        subprocess.run(["apptainer", "--quiet", "exec", "--contain", "--cleanenv", "--bind", f"{host}:{repo_path}", "--pwd", repo_path, str(sif),
                        "bash", "-c", "git init -q && git -c user.name=rsm -c user.email=rsm@local commit -q --allow-empty -m 'sandbox: empty workspace'"],
                       check=True, capture_output=True, text=True, timeout=120)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--order", type=int, required=True)
    ap.add_argument("--kind", choices=sorted(SYSTEMS), required=True)
    ap.add_argument("--memory-system", default=None, help="override the memory layer for kind=kv (default fusednote)")
    ap.add_argument("--sandbox", required=True, help="SIF basename without .sif under RSM_SIF_DIR")
    ap.add_argument("--step-limit", type=int, default=60)
    ap.add_argument("--served-name", default="coder-next")
    a = ap.parse_args()

    projects = load_projects()
    proj = projects.get(a.project)
    if not proj:
        sys.exit(f"unknown project {a.project}")
    task = next((t for t in proj["tasks"] if t["order"] == a.order), None)
    if not task:
        sys.exit(f"project {a.project} has no task {a.order}")
    mode = (a.memory_system or SYSTEMS[a.kind]) if a.kind != "none" else None
    arm = f"mem_{mode}" if mode else "seq"
    out = RUNS / "custom" / f"{a.project}__s0__{arm}"
    out.mkdir(parents=True, exist_ok=True)
    sif = SIF_DIR / f"{a.sandbox}.sif"
    if not sif.exists():
        sys.exit(f"sandbox image missing: {sif}")
    generic = a.sandbox.startswith("sandbox-")
    repo_path = "/work" if generic else G.detect_repo_dir(sif)
    prefix = "" if generic else G.detect_conda_prefix(sif)
    host = WORK / a.project / arm / "repo"
    if generic:
        ensure_git_workspace(host, sif, repo_path)
    env = RepoEnv(image=str(sif), host_repo=str(host), repo_path=repo_path, prefix=prefix)

    # memory layer + persisted store (one per project × arm)
    layer = mem_base.REGISTRY[mode]() if mode else None
    store_file = out / "store.json"
    if layer is not None:
        layer.chain_id, layer.task_idx, layer.out_dir = a.project, a.order, out
        try:
            layer.store.items = [tuple(x) for x in json.load(open(store_file))]
        except Exception:
            pass

    model_name, mk, ep_file = C.model_config(None, None, a.served_name, None)
    if os.environ.get("RSM_API_BASE"):
        mk["api_base"] = os.environ["RSM_API_BASE"]
    if _LEDGER:
        _LEDGER.install()
        _LEDGER.begin_session(run_id="custom", chain=a.project, slot=a.order, instance=task["instance"], arm=arm, out=out)

    task_base = head(env)
    t_wrap0 = time.time()
    w = layer.wrap(task["problem_statement"], env) if layer else mem_base.WrapResult(task["problem_statement"])
    wrap_secs = round(time.time() - t_wrap0, 1)
    if _LEDGER:
        _LEDGER.note_injected(w)
    model = LitellmTextbasedModel(model_name=model_name, model_kwargs=mk, **C.model_template_kwargs())
    agent = DefaultAgent(model, env, system_template=C.system_template(a.step_limit), instance_template=C.instance_template(repo_path),
                         step_limit=a.step_limit, cost_limit=K.COST_LIMIT, output_path=out / f"traj_{a.order}_{task['instance']}.json")
    print(f"=== custom project {a.project} task {a.order} arm={arm} sandbox={a.sandbox} model={a.served_name}", flush=True)
    t0 = time.time()
    err = None
    try:
        agent.run(task=w.text)
        exit_s = agent.messages[-1].get("extra", {}).get("exit_status", "?")
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:200]}"
        exit_s = f"error:{type(e).__name__}"
    rec = {"order": a.order, "instance": task["instance"], "arm": arm, "proto": K.PROTO_TAG, "exit": exit_s,
           "secs": round(time.time() - t0, 1), "wrap_secs": wrap_secs, "n_steps": agent.n_calls,
           "n_assistant_msgs": sum(1 for m in agent.messages if m.get("role") == "assistant"),
           "injected_chars": w.injected_chars, "injected": w.injected, "map_chars": w.map_chars,
           "error": err, "crashed": bool(err), "resolved": None, "gradable": False, "f2p": {}, "p2p_failed": [],
           "p2p_missing": [], "p2p_unscoped": [], "collection_errors": [], "error_stage": None, "custom": True,
           "sandbox": a.sandbox, "model": a.served_name}
    # harness commit (agent is banned from committing), then the true delta of this task
    sh(env, "git add -A && git -c user.name=rsm -c user.email=rsm@local commit -q --allow-empty -m 'rsm: task %d' || true" % a.order)
    delta = sh(env, f"git diff {task_base} HEAD") if len(task_base) == 40 else ""
    rec["delta_chars"] = len(delta)
    if layer is not None:
        t1 = time.time()
        try:
            spec = {"swebench_instance_id": task["instance"], "_rsm_resolved": None}
            rec["mem_nodes"] = layer.write(spec, ChainRunner._material(None, agent, delta), delta, env)
            rec["mem_write_secs"] = round(time.time() - t1, 1)
        except Exception as e:
            rec["mem_write_error"] = str(e)[:150]
        json.dump([list(x) for x in layer.store.items], open(store_file, "w"))
        (out / "mem_stats.json").write_text(json.dumps(layer.stats()))
    if _LEDGER:
        _LEDGER.harvest_actor(agent)
        rec.update(_LEDGER.session_summary())
    rf = out / "results.jsonl"
    recs = [json.loads(l) for l in rf.read_text().splitlines() if l.strip()] if rf.exists() else []
    recs = [r for r in recs if r.get("order") != a.order] + [rec]
    atomic_results(out, sorted(recs, key=lambda r: r["order"]))
    print(f"  [{a.project}|{arm}] task {a.order} {task['instance']}: exit={exit_s} steps={rec['n_steps']} inj={w.injected} "
          f"delta={len(delta)} chars {rec['secs']}s", flush=True)
    sys.path.insert(0, str(HERE))
    import scrub                                                  # mini-swe-agent saved the model config (api_key) into the traj
    scrub.scrub_dir(out, [os.environ.get("RSM_API_KEY", ""), os.environ.get("GOODVISION_API_KEY", "")])
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
