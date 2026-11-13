"""Run one user-written task in a sandbox with a memory system — the demo's "your own task" mode.

A *project* is a user-made chain: a sandbox image (a ChainSWE repo image at its base commit, or a plain
Python image) plus the tasks typed in the UI, numbered in order. Each memory arm (kv / mem0 / memop / none) keeps
its own persistent workspace and memory store under RUNS/custom/<project>__s0__<arm>/, so task k+1 inherits
the repository state and the notes written after task k — exactly the ChainSWE seq protocol, minus the
grading (there are no F2P/P2P tests for a free-form task: `resolved` is null, `gradable` false).
A generic-image project's workspaces start from its seed WORK/<project>/seed (the files the user uploaded or the git
repo they cloned; demo/server.py writes it); files the user adds later are committed as 'user: add files' before the
next task, so the agent's change (task_base..HEAD, the session's diff) never includes them.

Everything else is the harness's own machinery: RepoEnv (Apptainer), mini-swe-agent DefaultAgent with the
pinned templates, the registered memory layers, the per-task harness commit, the token ledger (RSM_LEDGER=1).
Output files have the same layout as a chain directory (traj_<k>_*.json, results.jsonl, calls.jsonl,
mem_stats.json), so demo/server.py serves these sessions like any other.

Usage (demo/server.py builds the environment — endpoint, keys, strict switches — exactly as for live chains):
  python demo/custom_runner.py --project NAME --order K --kind kv|mem0|memop|none [--memory-system fusednote]
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
from harness.live_agent import LiveSaveAgent as DefaultAgent            # noqa: E402  (== DefaultAgent unless RSM_LIVE_SAVE=1)
from harness import mem_snapshot as MS                                  # noqa: E402
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel  # noqa: E402
from harness.gateway_model import GatewayTextModel                       # noqa: E402  (== LitellmTextbasedModel unless RSM_OPENAI_STRICT=1)
import memory                                                           # noqa: E402  registers the layers
from memory import base as mem_base                                     # noqa: E402

_LEDGER = None
if os.environ.get("RSM_LEDGER") == "1":
    from harness import ledger as _LEDGER                               # noqa: E402

RUNS = pathlib.Path(os.environ.get("RSM_RUNS_DIR", str(ROOT / "runs")))
SIF_DIR = pathlib.Path(os.environ.get("RSM_SIF_DIR", str(ROOT / "sifs")))
WORK = pathlib.Path(os.environ.get("DEMO_CUSTOM_WORK", f"/scratch/{os.environ.get('USER', 'nobody')}/kv/custom"))
PROJECTS = RUNS / "custom" / "projects.json"
SYSTEMS = {"kv": "fusednote", "mem0": "factsall", "memop": "memop", "none": None}   # memop: its guidance is the store's one item


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


def store_before(out, order):
    """The memory store as it was right before task `order`: the newest per-task snapshot mem_store_<k>.json (MS.save)
    with k < order. A task that was stopped or crashed left no snapshot, and it wrote nothing to memory either, so the
    snapshot of the last task that finished is the right state. -> (items, k); ([], 0) when no earlier task wrote memory;
    (None, None) when a task after the newest snapshot did write memory (its snapshot is missing: the state is unknown)."""
    snaps = {}
    for p in out.glob("mem_store_*.json"):
        m = re.match(r"mem_store_(\d+)\.json$", p.name)
        if m and int(m.group(1)) < order:
            snaps[int(m.group(1))] = p
    k = max(snaps, default=0)
    rf = out / "results.jsonl"
    recs = [json.loads(l) for l in rf.read_text().splitlines() if l.strip()] if rf.exists() else []
    if any(isinstance(r.get("order"), int) and k < r["order"] < order and r.get("mem_nodes") for r in recs):
        return None, None
    if not k:
        return [], 0
    try:
        return json.load(open(snaps[k])).get("items", []), k
    except Exception:
        return None, None


def rewind(env, out, order, store_file, has_memory):
    """--fresh: put this project × arm back to the state right before task `order` was first run.
    Workspace: reset to the parent of the harness commit 'rsm: task <order>' (each task ends with one).
    Memory: the store as it was before task `order` (store_before: the newest earlier snapshot; empty when no earlier task
    wrote memory). Runs of tasks >= order on this arm are dropped: they started from the attempt being replaced."""
    shas = re.findall(r"\b[0-9a-f]{40}\b", sh(env, f"git log --format=%H --grep='^rsm: task {order}$'"))
    if shas:
        base = re.findall(r"\b[0-9a-f]{40}\b", sh(env, f"git rev-parse {shas[-1]}^"))   # oldest match = first attempt's commit
        if base:
            sh(env, f"git reset -q --hard {base[-1]} && git clean -fdq")
            print(f"rewound workspace to {base[-1][:10]} (before task {order})", flush=True)
    else:
        # the attempt being replaced never finished (■ Stop): no 'rsm: task <order>' commit, its edits are still uncommitted in
        # the working tree. Drop them, so the rerun starts where that attempt started and its edits are not later committed as
        # the user's files. Files the user uploaded meanwhile come back from the seed (restore_user_files). If a later task ran
        # since, its start committed those edits and there is no clean point to go back to: keep the workspace, say so.
        done = [int(x) for x in re.findall(r"^rsm: task (\d+)$", sh(env, "git log --format=%s"), re.M)]
        if not any(x > order for x in done):
            sh(env, "git reset -q --hard && git clean -fdq")
            print(f"dropped the changes of task {order}'s unfinished attempt", flush=True)
        else:
            print(f"warning: task {order} never finished and later tasks ran since; keeping the workspace as it is", flush=True)
    if has_memory:
        items, k = store_before(out, order)
        if items is None:
            print(f"warning: no memory snapshot of the state before task {order}; keeping the current store", flush=True)
        else:
            json.dump(items, open(store_file, "w"))
            print(f"memory store reset to {len(items)} item(s) ({f'state after task {k}' if k else 'empty: no earlier task wrote memory'})", flush=True)
    for p in out.glob("traj_*.json"):
        m = re.match(r"traj_(\d+)_", p.name)
        if m and int(m.group(1)) > order:
            p.unlink()
    for p in out.glob("mem_store_*.json"):
        m = re.match(r"mem_store_(\d+)\.json$", p.name)
        if m and int(m.group(1)) >= order:
            p.unlink()
    rf = out / "results.jsonl"
    if rf.exists():
        recs = [json.loads(l) for l in rf.read_text().splitlines() if l.strip()]
        atomic_results(out, [r for r in recs if r.get("order", 0) < order])


def copy_seed(host, seed):
    """First task of this project x memory system: the workspace starts as a copy of the project's seed
    (WORK/<project>/seed = the files the user uploaded or the repo they cloned, .git included, symlinks kept as links).
    Only when the workspace is missing or empty and the seed has something in it. -> True when copied."""
    host = pathlib.Path(host)
    if seed is None:
        return False
    seed = pathlib.Path(seed)
    try:
        if seed.is_symlink() or not seed.is_dir() or not any(seed.iterdir()):
            return False
        if host.exists() and any(host.iterdir()):
            return False
    except OSError:
        return False
    shutil.copytree(seed, host, symlinks=True, dirs_exist_ok=True)
    return True


def ensure_git_workspace(host, sif, repo_path, seed=None):
    """Generic sandboxes have no repository: the bound workspace becomes a copy of the project's seed (copy_seed) and a
    git repo. A seed that brought its own history (a cloned repo, a folder with .git) keeps it; otherwise everything is
    committed once — 'sandbox: initial files', or 'sandbox: empty workspace' when there are no files."""
    host = pathlib.Path(host)
    copied = copy_seed(host, seed)
    host.mkdir(parents=True, exist_ok=True)
    if not (host / ".git").exists():
        msg = "sandbox: initial files" if any(p.name != ".git" for p in host.iterdir()) else "sandbox: empty workspace"
        subprocess.run(["apptainer", "--quiet", "exec", "--contain", "--cleanenv", "--bind", f"{host}:{repo_path}", "--pwd", repo_path, str(sif),
                        "bash", "-c", f"git init -q && git add -A && git -c user.name=rsm -c user.email=rsm@local commit -q --allow-empty -m '{msg}'"],
                       check=True, capture_output=True, text=True, timeout=600)
    return copied


USER_FILES = ("if [ -n \"$(git status --porcelain 2>/dev/null)\" ]; then git add -A && "
              "git -c user.name=rsm -c user.email=rsm@local commit -q -m 'user: add files' && echo RSM_USER_FILES_COMMITTED; fi")


def commit_user_files(env):
    """Files the user added since the previous task (uploads land in every workspace) are committed before the task
    starts, so they are not counted as the agent's change (task_base is taken after this)."""
    if "RSM_USER_FILES_COMMITTED" in sh(env, USER_FILES):
        print("committed the files the user added since the last task ('user: add files')", flush=True)


def restore_user_files(env, host, seed):
    """--fresh rewound the workspace (git reset --hard + clean), which drops files the user uploaded after earlier tasks
    if they were never committed in the history that remains. Copy back every seed file whose path that history never
    contained; paths it did contain stay exactly as git restored them (the agent may have changed or deleted them).
    Best effort: links in the workspace are never followed. -> number of files restored."""
    seed = pathlib.Path(seed)
    if not seed.is_dir() or seed.is_symlink():
        return 0
    known = set(sh(env, "git -c core.quotePath=false log --format= --name-only --diff-filter=A HEAD 2>/dev/null").splitlines())
    n = 0
    for root, dirs, files in os.walk(seed):
        rel_root = os.path.relpath(root, seed)
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d != ".git"]           # the seed's own git metadata is not a user file
        for f in files:
            rel = os.path.normpath(os.path.join(rel_root, f))
            if rel in known:
                continue
            dst, ok = host, True
            for comp in rel.split(os.sep)[:-1]:
                dst = dst / comp
                if dst.is_symlink() or (dst.exists() and not dst.is_dir()):
                    ok = False
                    break
            dst = host / rel
            if not ok or os.path.lexists(dst):
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = pathlib.Path(root) / f
            if src.is_symlink():
                os.symlink(os.readlink(src), dst)
            else:
                shutil.copy2(src, dst)
            n += 1
    if n:
        print(f"restored {n} file(s) the user had added (they were not in the rewound history)", flush=True)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--order", type=int, required=True)
    ap.add_argument("--kind", choices=sorted(SYSTEMS), required=True)
    ap.add_argument("--memory-system", default=None, help="override the memory layer for kind=kv (default fusednote)")
    ap.add_argument("--sandbox", required=True, help="SIF basename without .sif under RSM_SIF_DIR")
    ap.add_argument("--step-limit", type=int, default=60)
    ap.add_argument("--served-name", default="coder-next")
    ap.add_argument("--generic", action="store_true", help="an empty-workspace image (not a chain image): work in /work, git initialised")
    ap.add_argument("--fresh", action="store_true", help="rerun: rewind this arm's memory store and workspace to just before the task "
                                                          "and discard this arm's runs of later tasks (they were built on the old attempt)")
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
    generic = a.generic or a.sandbox.startswith("sandbox-")
    repo_path = "/work" if generic else G.detect_repo_dir(sif)
    prefix = "" if generic else G.detect_conda_prefix(sif)
    host = WORK / a.project / arm / "repo"
    seed = WORK / a.project / "seed"                      # the user's files / cloned repo (demo/server.py writes it)
    if generic:
        if ensure_git_workspace(host, sif, repo_path, seed):
            print("workspace starts as a copy of the project's files", flush=True)
    env = RepoEnv(image=str(sif), host_repo=str(host), repo_path=repo_path, prefix=prefix)
    store_file = out / "store.json"
    if a.fresh:
        rewind(env, out, a.order, store_file, mode is not None)
        if generic:
            try:
                restore_user_files(env, host, seed)
            except Exception as e:                        # never fatal: the rerun works without them
                print(f"warning: could not restore the user's files after the rewind: {type(e).__name__}: {e}", flush=True)

    # memory layer + persisted store (one per project × arm)
    layer = mem_base.REGISTRY[mode]() if mode else None
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

    if generic:
        commit_user_files(env)
    task_base = head(env)
    t_wrap0 = time.time()
    w = layer.wrap(task["problem_statement"], env) if layer else mem_base.WrapResult(task["problem_statement"])
    wrap_secs = round(time.time() - t_wrap0, 1)
    if _LEDGER:
        _LEDGER.note_injected(w)
    model = GatewayTextModel(model_name=model_name, model_kwargs=mk, **C.model_template_kwargs())
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
    # v5inline: the agent writes its own note as the last turn of this session (same call site as harness/mini_swe_agent.py);
    # layer.write() then stores it, or falls back to the separate v5 writer when that turn failed
    if layer is not None and getattr(layer, "inline_self_write", False):
        from harness import inline_note as INL
        layer.pending_inline = INL.run_inline_turn(agent=agent, model=model, exit_status=exit_s, out=out, chain=a.project,
                                                   order=a.order, instance=task["instance"], arm=arm, run_id="custom")
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
            rec.update(getattr(layer, "rec_extra", None) or {})          # v5inline: note_write_mode, note tokens, …
        except Exception as e:
            rec["mem_write_error"] = str(e)[:150]
        json.dump([list(x) for x in layer.store.items], open(store_file, "w"))
        MS.save(out, a.order, layer, rec.get("mem_nodes"), force=True)   # per-task snapshot for the memory comparison view
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
