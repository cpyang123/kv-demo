"""Knowledge Vault × ChainSWE cost demo — backend over the harness.

Serves the recorded runs under runs/<run-id> and can launch live chains through
harness/mini_swe_agent.py (RSM_LEDGER=1). Token and USD meters reuse
harness.ledger.summarize / cost_usd and harness/prices.json, so a number shown here is
the number analysis/ledger_table would print for the same session.

Arms shown by the UI (env DEMO_ARMS overrides, JSON {kind: arm}):
    none -> seq            no cross-session memory
    mem0 -> mem_factsall   mem0 fact extraction (vendored official prompts), full injection
    kv   -> mem_fusednote  Knowledge Vault note (fused template writer)

Token sources per session, in priority order (field tok.src):
    ledger    calls.jsonl written by harness/ledger.py (live runs, exact, every role)
    traj      usage of the actor's own calls from traj_<k>_*.json (recorded pre-ledger runs);
              the writer side is then *estimated* from the material the harness handed the
              writer (tok.writer_src = "estimate") — flagged as such in the UI.

Run:  uvicorn demo.server:app --port 8765      (from the repo root)
"""
from __future__ import annotations

import collections
import glob
import json
import os
import pathlib
import shutil
import statistics as st
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from harness import ledger as L                                  # noqa: E402  pure functions only
import providers as PV                                           # noqa: E402  model endpoints (local vLLM, GoodVision SmartRouter)
import scrub                                                     # noqa: E402  removes API keys mini-swe-agent writes into traj files

RUNS = pathlib.Path(os.environ.get("RSM_RUNS_DIR", str(ROOT / "runs")))
SIF_DIR = pathlib.Path(os.environ.get("RSM_SIF_DIR", str(ROOT / "sifs")))
CHAIN_DATA = pathlib.Path(os.environ.get("RSM_CHAIN_DATA", str(ROOT / "data/chainswe_data.jsonl")))
LINK_FLAGS = ROOT / "data/task_link_flags_v2.json"
LIVE_DIR = pathlib.Path(os.environ.get("DEMO_LIVE_DIR", str(HERE / "live")))
DEFAULT_RUN = os.environ.get("DEMO_RUN", "v3main100")
ARMS = json.loads(os.environ.get("DEMO_ARMS", '{"none": "seq", "mem0": "mem_factsall", "kv": "mem_fusednote"}'))
LIVE_SYSTEMS = {"none": None, "mem0": "factsall", "kv": "fusednote"}
KV_SYSTEMS = ["fusednote", "v5knote", "v5inline", "v4knote"]
MEM0_SYSTEMS = ["factsall"]

# v3.0 protocol environment (TEAM_GUIDE §1) — what the recorded v3main100 runs used.
V3_ENV = {
    "RSM_ACTOR_NOTHINK": "1", "RSM_MODEL_TPL": "1", "RSM_LOCAL_MAX_TOKENS": "16384",
    "RSM_PROTO_TAG": "v3.0", "RSM_GRADE_TIMEOUT": "5400",
    "RSM_RAW_MSG_CAP": "999999", "RSM_RAW_TOTAL_CAP": "999999", "RSM_RAW_FINAL_CAP": "220000",
    "RSM_DISTILL_CAP": "220000",
}
# Demo runs also enable the writer validator's v2 rule (memory/ours/fusednote.py: the bare "mswea_bash_command"
# substring check is dropped; fenced code is still rejected). Without it a note that merely *mentions* the tool
# name is thrown away ("FUSED_DISTILL_INVALID"), which happened on the first router run. Env-gated upstream.
V3_ENV["RSM_VALID_V2"] = "1"
FACTS_INPUT_CAP = 23000          # memory/attribution/probes.py default

MODEL_PRICES_FILE = HERE / "model_prices.json"
CONFIGS_FILE = HERE / "configs.json"


def _model_prices():
    try:
        return json.load(open(MODEL_PRICES_FILE)).get("models", {})
    except Exception:
        return {}


def _price_for_model(model, table):
    """Longest-prefix match of a (possibly dated) model id against the per-model price table; None if unknown."""
    if not model:
        return None, None
    best = None
    for k in table:
        if model == k or model.startswith(k + "-") or model.startswith(k):
            if best is None or len(k) > len(best):
                best = k
    return (table[best], best) if best else (None, None)


def _usd_by_model(rows, table):
    """Price each ledger row by the model that served it (fallback: the global price row). -> (usd, by_model, unknown)."""
    pr = L.price_row(); usd = 0.0; by = collections.Counter(); unknown = set()
    for r in rows:
        p, key = _price_for_model(r.get("model"), table)
        if p is None:
            p = {"input": pr["input"], "cached_input": pr["cached_input"], "output": pr["output"]}; key = r.get("model") or "model not recorded"; unknown.add(key)
        pt = int(r.get("prompt_tokens") or 0); cc = min(int(r.get("cached_tokens") or 0), pt)
        usd += ((pt - cc) * p["input"] + cc * p["cached_input"] + int(r.get("completion_tokens") or 0) * p["output"]) / 1e6
        by[key] += 1
    return round(usd, 6), dict(by), sorted(unknown)
MEM_HEAD = "<memory_context>"
MEM_TAIL = "</memory_context>"

app = FastAPI(title="Knowledge Vault × ChainSWE demo")


# ---------------------------------------------------------------- dataset

def _load_chains():
    out = {}
    for l in open(CHAIN_DATA):
        if not l.strip():
            continue
        r = json.loads(l)
        tasks = sorted(r["bug_fixes"], key=lambda b: b["order"])
        out[r["continuous_id"]] = {
            "cid": r["continuous_id"], "repo": r["repo"], "source": r.get("source"),
            "date_range": r.get("date_range"), "docker_image": r["docker_image"],
            "tag": r["docker_image"].split(":")[1], "n_tasks": len(tasks),
            "tasks": [{"order": b["order"], "instance": b["swebench_instance_id"],
                       "title": (b.get("problem_statement") or "").strip().splitlines()[0][:160]
                       if (b.get("problem_statement") or "").strip() else "",
                       "problem_statement": b.get("problem_statement") or ""} for b in tasks],
        }
    return out


CHAINS = _load_chains()
try:
    LINKS = json.load(open(LINK_FLAGS))
except Exception:
    LINKS = {}


def _link_flag(cid, order):
    v = LINKS.get(f"{cid}|{order}")
    if isinstance(v, dict):
        v = v.get("linked", v.get("flag"))
    return v


def _sif_path(cid):
    return SIF_DIR / f"{CHAINS[cid]['tag']}.sif"


def _sif_ok(cid):
    p = _sif_path(cid)
    try:
        return p.exists() and p.stat().st_size >= 100_000_000
    except OSError:
        return False


# ---------------------------------------------------------------- run loading

def _read_jsonl(p):
    try:
        return [json.loads(l) for l in open(p).read().split("\n") if l.strip()]
    except Exception:
        return []


def _read_calls(d):
    """calls.jsonl -> {slot: rows}; last-attempt rule as in analysis/ledger_table/ledger_rollup.py."""
    out = collections.defaultdict(list)
    f = pathlib.Path(d) / "calls.jsonl"
    if not f.exists():
        return out
    for r in _read_jsonl(f):
        rows = out[r.get("slot")]
        ci = r.get("call_idx")
        if rows and isinstance(ci, int) and isinstance(rows[-1].get("call_idx"), int) and ci <= rows[-1]["call_idx"]:
            rows.clear()
        rows.append(r)
    return out


def _traj_file(d, order):
    fs = sorted(glob.glob(f"{d}/traj_{order}_*.json"))
    return fs[0] if fs else None


def _load_traj(d, order):
    f = _traj_file(d, order)
    if not f:
        return None
    try:
        return json.load(open(f))
    except Exception:
        return None


def _actor_rows_from_traj(traj):
    rows = []
    for m in (traj or {}).get("messages", []):
        u = (((m.get("extra") or {}).get("response") or {}) or {}).get("usage") or {}
        if not u:
            continue
        det = u.get("prompt_tokens_details") or {}
        pt = int(u.get("prompt_tokens") or 0)
        cc = min(int(det.get("cached_tokens") or 0), pt)
        rows.append({"role": "actor", "prompt_tokens": pt, "cached_tokens": cc,
                     "uncached_prompt_tokens": pt - cc,
                     "completion_tokens": int(u.get("completion_tokens") or 0),
                     "cached_src": "usage" if det else "none"})
    return rows


def _memory_block(traj):
    """Payload injected into the task prompt (what the agent saw before this task)."""
    for m in (traj or {}).get("messages", []):
        if m.get("role") == "user":
            c = m.get("content") or ""
            if MEM_HEAD in c:
                body = c.split(MEM_HEAD, 1)[1]
                body = body.split(MEM_TAIL, 1)[0].strip()
                head = "Notes from earlier work sessions on this repository:"   # MEM_WRAP_HEAD's 2nd line
                return body[len(head):].strip() if body.startswith(head) else body
            return ""
    return ""


def _material(traj):
    """Writer input as harness/mini_swe_agent.py::_material builds it under the v3 caps
    (RSM_RAW_MSG_CAP/TOTAL_CAP 999999, FINAL_CAP 220000); the FINAL DIFF is not stored, so it is
    left out — the estimate errs low."""
    parts = []
    for m in (traj or {}).get("messages", []):
        r, c = m.get("role"), m.get("content")
        if r == "assistant" and isinstance(c, str):
            parts.append("ASSISTANT:\n" + c)
        elif r == "user" and parts and isinstance(c, str):
            parts.append("OBSERVATION:\n" + c)
    return "\n\n".join(parts)[:220000]


_PROMPT_CACHE = {}


def _prompt_chars(rel):
    if rel not in _PROMPT_CACHE:
        try:
            _PROMPT_CACHE[rel] = len((ROOT / rel).read_text())
        except Exception:
            _PROMPT_CACHE[rel] = 0
    return _PROMPT_CACHE[rel]


def _writer_rows_estimate(kind, traj, note_written):
    """Writer-side calls of a recorded (pre-ledger) session, estimated with the ledger's own
    char->token rule (harness.ledger._tok_est). kv = one fused-note distill call; mem0 = the two
    official extraction branches (agent + user prompt), each over material[:23000]."""
    mat = _material(traj)
    rows = []
    if kind == "kv":
        pt = L._tok_est(mat[:220000]) + L._tok_est(" " * _prompt_chars("memory/ours/prompts/fused_writer_prompt.txt"))
        rows.append({"role": "note_writer", "prompt_tokens": pt, "cached_tokens": 0,
                     "uncached_prompt_tokens": pt, "completion_tokens": L._tok_est(note_written),
                     "cached_src": "estimate"})
    elif kind == "mem0":
        from memory.attribution import mem0_prompts as MP
        for sp in (MP.AGENT_MEMORY_EXTRACTION_PROMPT, MP.USER_MEMORY_EXTRACTION_PROMPT):
            pt = L._tok_est(sp) + L._tok_est(mat[:FACTS_INPUT_CAP]) + 4
            rows.append({"role": "memory_extract", "prompt_tokens": pt, "cached_tokens": 0,
                         "uncached_prompt_tokens": pt, "completion_tokens": L._tok_est(note_written) // 2,
                         "cached_src": "estimate"})
    return rows


def _note_written(blocks, order, n_tasks):
    """Store delta attributed to session `order` = what the next task saw minus what this one saw."""
    if order >= n_tasks:
        return ""
    nxt, cur = blocks.get(order + 1, ""), blocks.get(order, "")
    if not nxt:
        return ""
    if cur and nxt.startswith(cur):
        return nxt[len(cur):].strip()
    if cur and cur in nxt:
        return nxt.replace(cur, "", 1).strip()
    return nxt.strip() if not cur else nxt[-max(0, len(nxt) - len(cur)):].strip()


_CACHE = {}


def load_chain_arm(run, arm, cid, kind=None):
    """-> {cid, arm, complete, sessions:[...]} or None if the run has no directory for it."""
    d = RUNS / run / f"{cid}__s0__{arm}"
    rf = d / "results.jsonl"
    if not rf.exists():
        return None
    try:
        key = (run, arm, cid, rf.stat().st_mtime_ns, (d / "calls.jsonl").stat().st_mtime_ns if (d / "calls.jsonl").exists() else 0)
    except OSError:
        return None
    if key in _CACHE:
        return _CACHE[key]
    kind = kind or next((k for k, a in ARMS.items() if a == arm), "kv" if arm.startswith("mem_") else "none")
    n_tasks = CHAINS[cid]["n_tasks"] if cid in CHAINS else 0
    rows = {r["order"]: r for r in _read_jsonl(rf) if "order" in r}
    calls = _read_calls(d)
    trajs = {k: _load_traj(d, k) for k in rows}
    blocks = {k: _memory_block(t) for k, t in trajs.items()}
    sessions = []
    for k in sorted(rows):
        r = rows[k]
        note = _note_written(blocks, k, n_tasks) if kind != "none" else ""
        if calls.get(k):
            tok_rows, src, wsrc = calls[k], "ledger", "ledger"
        else:
            tok_rows, src = _actor_rows_from_traj(trajs[k]), "traj"
            est = _writer_rows_estimate(kind, trajs[k], note) if kind != "none" else []
            wsrc = "estimate" if est else "none"
            tok_rows = tok_rows + est
        s = L.summarize(tok_rows)
        mp = _model_prices()
        usd_model, by_model, unknown = _usd_by_model(tok_rows, mp)
        usd_model_actor, _, _ = _usd_by_model([x for x in tok_rows if x.get("role") == "actor"], mp)
        sessions.append({
            "order": k, "instance": r.get("instance"), "resolved": r.get("resolved"),
            "gradable": r.get("gradable"), "exit": r.get("exit"), "n_steps": r.get("n_steps"),
            "secs": r.get("secs"), "error": r.get("error"), "injected": r.get("injected"),
            "injected_chars": r.get("injected_chars"), "linked": _link_flag(cid, k),
            "memory_text": blocks.get(k, ""), "note_written": note,
            "traj_file": os.path.basename(_traj_file(d, k) or ""),
            "tok": {
                "src": src, "writer_src": wsrc,
                "actor_prompt": s["tok_actor_prompt"], "actor_cached": s["tok_actor_cached"],
                "actor_completion": s["tok_actor_completion"], "n_calls_actor": s["n_calls_actor"],
                "writer_prompt": s["tok_writer_prompt"] + s["tok_memother_prompt"],
                "writer_completion": s["tok_writer_completion"] + s["tok_memother_completion"],
                "raw_total": s["tok_raw_total"], "cacheaware_total": s["tok_cacheaware_total"],
                "usd": s["cost_usd_priced"],
                "usd_actor": round(L.cost_usd([x for x in tok_rows if x.get("role") == "actor"]), 6),
                "usd_model": usd_model, "usd_model_actor": usd_model_actor, "usd_model_writer": round(usd_model - usd_model_actor, 6),
                "models": by_model, "models_unpriced": unknown,
            },
        })
    out = {"cid": cid, "arm": arm, "kind": kind, "dir": str(d),
           "complete": len(sessions) == n_tasks and all(s["resolved"] is not None or s["gradable"] is False for s in sessions),
           "sessions": sessions}
    _CACHE[key] = out
    return out


def _set_price(row):
    if row:
        os.environ["RSM_PRICE_ROW"] = row
    else:
        os.environ.pop("RSM_PRICE_ROW", None)
    L._PRICE["row"] = None          # harness.ledger caches the row; reset so the choice applies
    _CACHE.clear()                  # USD is baked into the cached sessions


def _runs():
    if not RUNS.exists():
        return []
    return sorted(p.name for p in RUNS.iterdir() if p.is_dir() and any(p.glob("*__s0__*")))


def _chains_in_run(run):
    cids = set()
    for p in (RUNS / run).glob("*__s0__*"):
        cids.add(p.name.split("__")[0])
    return sorted(c for c in cids if c in CHAINS)


# ---------------------------------------------------------------- API: metadata

ENDPOINT_FILE = pathlib.Path(os.environ.get("DEMO_ENDPOINT_FILE", f"/scratch/{os.environ.get('USER', 'nobody')}/kv/actor_endpoint"))


def _default_api_base():
    """RSM_API_BASE, else the endpoint demo/slurm/serve_actor.sbatch published, else a placeholder."""
    if os.environ.get("RSM_API_BASE"):
        return os.environ["RSM_API_BASE"]
    try:
        ep = ENDPOINT_FILE.read_text().strip()
        if ep and _endpoint_ok(ep):
            return ep
    except OSError:
        pass
    return "http://127.0.0.1:8000/v1"


@app.get("/api/meta")
def meta():
    prices = json.load(open(L.price_table_path()))
    return {
        "runs": _runs(), "default_run": DEFAULT_RUN if DEFAULT_RUN in _runs() else (_runs()[0] if _runs() else None),
        "arms": ARMS, "live_systems": {"kv": KV_SYSTEMS, "mem0": MEM0_SYSTEMS},
        "price_rows": prices["rows"], "default_price_row": prices.get("_default_row"),
        "sif_dir": str(SIF_DIR), "runs_dir": str(RUNS),
        "chains": [{"cid": c["cid"], "repo": c["repo"], "n_tasks": c["n_tasks"], "sif": _sif_ok(c["cid"])}
                   for c in CHAINS.values()],
        "default_api_base": _default_api_base(),
        "default_served_name": os.environ.get("DEMO_SERVED_NAME", "coder-next"),
        "providers": [{"name": n, "label": p["label"], "has_key": bool(PV.api_key(n)), "default_model": p["default_model"],
                       "models_hint": p["models_hint"], "api_base": p["api_base"] or _default_api_base(), "strict": p["strict"],
                       "console": p.get("console"), "docs": p["docs"]} for n, p in PV.PROVIDERS.items()],
        "default_provider": os.environ.get("DEMO_PROVIDER") or ("goodvision-test" if PV.api_key("goodvision-test") else "local"),
        "default_cid": os.environ.get("DEMO_DEFAULT_CID", "pyccel_pyccel_20240307"),   # the showcase chain (see demo/README.md)
        "live_enabled": os.environ.get("DEMO_DISABLE_LIVE") != "1",   # hosted replay-only deployments set DEMO_DISABLE_LIVE=1 (demo/deploy/README.md)
    }


class ChatReq(BaseModel):
    """Free chat with a model endpoint (the UI's 'chat with the model' mode); history is kept by the page."""
    messages: list
    provider: str = "local"
    model: str | None = None
    max_tokens: int = 900


@app.post("/api/chat")
def chat(req: ChatReq):
    api, key, strict, model = _resolve_endpoint(req.provider, req.model)
    msgs = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))[:20000]} for m in req.messages][-30:]
    body = {"model": model, "messages": [{"role": "system", "content": "You are a helpful assistant in a demo of the Knowledge Vault × ChainSWE project; answer concisely."}] + msgs}
    if strict:
        body["max_completion_tokens"] = req.max_tokens
    else:
        body.update({"temperature": 0.7, "max_tokens": req.max_tokens, "chat_template_kwargs": {"enable_thinking": False}})
    t0 = time.time()
    j = _chat_completion(api, key, body)
    return {"answer": j["choices"][0]["message"]["content"].split("</think>")[-1].strip(), "usage": j.get("usage"), "model": j.get("model"),
            "latency_s": round(time.time() - t0, 2), "request_id": j.get("_request_id")}


def _resolve_endpoint(provider, model):
    """-> (api_base, key, strict, model) for a usable endpoint, or HTTPException 503 with the router's own code."""
    if provider == "local":
        api = _default_api_base().rstrip("/")
        if not _endpoint_ok(api):
            raise HTTPException(503, f"no model endpoint at {api} — start demo/slurm/serve_actor.sbatch")
        return api, None, False, model or os.environ.get("DEMO_SERVED_NAME", "coder-next")
    c = PV.check(provider)
    if not c["ok"]:
        raise HTTPException(503, f"{c['label']}: {c['code']} — {c['message']}")
    return c["api_base"], PV.api_key(provider), c["strict"], model or c["default_model"]


def _chat_completion(api, key, body, timeout=240):
    hdr = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
    r = urllib.request.Request(api + "/chat/completions", json.dumps(body).encode(), hdr)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            j = json.load(resp)
            j["_request_id"] = resp.headers.get("x-gv-request-id") or resp.headers.get("x-request-id")
            return j
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise HTTPException(502, f"model call failed ({e.code}): {detail}")
    except Exception as e:
        raise HTTPException(502, f"model call failed: {type(e).__name__}: {str(e)[:120]}")


@app.get("/api/providers")
def providers_status():
    """Live status of every endpoint the demo can run against (one /models call each; never raises)."""
    out = []
    for n in PV.PROVIDERS:
        c = PV.check(n, api_base=_default_api_base() if n == "local" else None)
        out.append(c)
    return out


def _provider_env(name, model, api_base=None):
    """Harness environment for a live run through `name`; raises HTTPException when the endpoint is not usable."""
    if name not in PV.PROVIDERS:
        raise HTTPException(400, f"unknown provider {name}")
    if name == "local":
        base = api_base or _default_api_base()
        if not _endpoint_ok(base):
            raise HTTPException(400, f"no OpenAI-compatible endpoint answering at {base}/models")
        env = dict(V3_ENV)
        env.update(PV.env_for_harness("local", model, api_base=base))
        return env, base
    c = PV.check(name, api_base=api_base)
    if not c["ok"]:
        raise HTTPException(400, f"{c['label']}: {c['code']} — {c['message']}")
    if c["models"] and model not in c["models"] and model != "auto":
        raise HTTPException(400, f"model '{model}' is not in the {c['label']} model market ({len(c['models'])} ids; pick one from /api/providers)")
    env = {k: v for k, v in V3_ENV.items() if k != "RSM_ACTOR_NOTHINK"}     # vLLM-only switch
    env.update(PV.env_for_harness(name, model, api_base=api_base))
    return env, c["api_base"]


# ---------------------------------------------------------------- API: summary

@app.get("/api/summary")
def summary(run: str = DEFAULT_RUN, price: str | None = None):
    if run not in _runs():
        raise HTTPException(404, f"run {run} not found under {RUNS}")
    _set_price(price)
    per_arm = {k: {} for k in ARMS}
    for cid in _chains_in_run(run):
        for kind, arm in ARMS.items():
            x = load_chain_arm(run, arm, cid, kind)
            if x and x["complete"]:
                per_arm[kind][cid] = x
    common = sorted(set.intersection(*[set(v) for v in per_arm.values() if v]) if any(per_arm.values()) else set())
    agg, chains = {}, []
    for kind in ARMS:
        if not per_arm[kind]:                       # arm never run in this run-id (e.g. custom projects with one arm)
            agg[kind] = None
            continue
        ss = [s for cid in common for s in per_arm[kind][cid]["sessions"]]
        later = [s for s in ss if s["order"] >= 2]
        if not ss:
            agg[kind] = None
            continue
        def mean(xs):
            xs = [x for x in xs if x is not None]
            return round(st.mean(xs), 4) if xs else None
        agg[kind] = {
            "arm": ARMS[kind], "n_chains": len(common), "n_sessions": len(ss),
            "resolve_all": mean([1.0 if s["resolved"] else 0.0 for s in ss]),
            "resolve_later": mean([1.0 if s["resolved"] else 0.0 for s in later]),
            "resolve_pos1": mean([1.0 if s["resolved"] else 0.0 for s in ss if s["order"] == 1]),
            "steps": mean([s["n_steps"] for s in ss]),
            "steps_later": mean([s["n_steps"] for s in later]),
            "actor_k": mean([(s["tok"]["actor_prompt"] + s["tok"]["actor_completion"]) / 1000 for s in ss]),
            "actor_k_later": mean([(s["tok"]["actor_prompt"] + s["tok"]["actor_completion"]) / 1000 for s in later]),
            "writer_k": mean([(s["tok"]["writer_prompt"] + s["tok"]["writer_completion"]) / 1000 for s in ss]),
            "total_k": mean([s["tok"]["raw_total"] / 1000 for s in ss]),
            "cacheaware_k": mean([s["tok"]["cacheaware_total"] / 1000 for s in ss]),
            "usd_session": mean([s["tok"]["usd"] for s in ss]),
            "usd_chain": mean([sum(s["tok"]["usd"] for s in per_arm[kind][cid]["sessions"]) for cid in common]),
            "usd_actor_chain": mean([sum(s["tok"]["usd_actor"] for s in per_arm[kind][cid]["sessions"]) for cid in common]),
            "injected_chars_later": mean([s["injected_chars"] or 0 for s in later]),
            "writer_src": collections.Counter(s["tok"]["writer_src"] for s in ss).most_common(1)[0][0],
            "tok_src": collections.Counter(s["tok"]["src"] for s in ss).most_common(1)[0][0],
        }
    for cid in common:
        row = {"cid": cid, "repo": CHAINS[cid]["repo"], "n_tasks": CHAINS[cid]["n_tasks"]}
        for kind in ARMS:
            if cid not in per_arm[kind]:
                continue
            ss = per_arm[kind][cid]["sessions"]
            row[kind] = {"usd": round(sum(s["tok"]["usd"] for s in ss), 4),
                         "tokens_k": round(sum(s["tok"]["raw_total"] for s in ss) / 1000, 1),
                         "resolved": sum(1 for s in ss if s["resolved"]),
                         "steps": sum(s["n_steps"] or 0 for s in ss)}
        chains.append(row)
    return {"run": run, "price_row": L.price_row().get("_row"), "price": L.price_row(),
            "common_chains": len(common), "arms": agg, "chains": chains,
            "coverage": {k: len(v) for k, v in per_arm.items()}}


# ---------------------------------------------------------------- API: chains of a run (session list)

@app.get("/api/runchains")
def runchains(run: str, price: str | None = None):
    """Every chain that has any arm directory in the run, with per-kind status — the left-hand session list."""
    if run not in _runs():
        raise HTTPException(404, f"run {run} not found")
    _set_price(price)
    out = []
    for cid in _chains_in_run(run):
        row = {"cid": cid, "repo": CHAINS[cid]["repo"], "n_tasks": CHAINS[cid]["n_tasks"], "sif": _sif_ok(cid), "arms": {}}
        for kind, arm in ARMS.items():
            x = load_chain_arm(run, arm, cid, kind)
            if not x:
                continue
            row["arms"][kind] = {
                "arm": arm, "complete": x["complete"],
                "sessions": [{"order": s["order"], "instance": s["instance"], "resolved": s["resolved"], "n_steps": s["n_steps"],
                              "usd": s["tok"]["usd"], "usd_model": s["tok"]["usd_model"], "models": s["tok"]["models"],
                              "tokens": s["tok"]["raw_total"], "injected_chars": s["injected_chars"], "exit": s["exit"]} for s in x["sessions"]],
                "usd": round(sum(s["tok"]["usd"] for s in x["sessions"]), 4),
                "usd_model": round(sum(s["tok"]["usd_model"] for s in x["sessions"]), 4),
                "resolved": sum(1 for s in x["sessions"] if s["resolved"]),
            }
        if row["arms"]:
            out.append(row)
    return {"run": run, "chains": out}


# ---------------------------------------------------------------- API: one chain

@app.get("/api/chain/{cid}")
def chain(cid: str, run: str = DEFAULT_RUN, price: str | None = None):
    if cid not in CHAINS:
        raise HTTPException(404, "unknown chain")
    _set_price(price)
    out = {"chain": CHAINS[cid], "run": run, "sif": _sif_ok(cid), "sif_path": str(_sif_path(cid)), "arms": {}}
    if run in _runs():
        for kind, arm in ARMS.items():
            x = load_chain_arm(run, arm, cid, kind)
            if x:
                out["arms"][kind] = x
        # any other arm directories present for this chain (e.g. live runs with another memory system)
        for p in (RUNS / run).glob(f"{cid}__s0__*"):
            arm = p.name.split("__")[-1]
            if arm not in ARMS.values():
                kind = "kv" if arm.startswith("mem_") and "facts" not in arm else ("mem0" if "facts" in arm else "none")
                x = load_chain_arm(run, arm, cid, kind)
                if x:
                    out["arms"][arm] = x
    return out


@app.get("/api/traj")
def traj(run: str, arm: str, cid: str, order: int, max_chars: int = 6000):
    d = RUNS / run / f"{cid}__s0__{arm}"
    t = _load_traj(d, order)
    if not t:
        raise HTTPException(404, "trajectory not found")
    msgs = []
    for m in t.get("messages", []):
        c = m.get("content") or ""
        u = (((m.get("extra") or {}).get("response") or {}) or {}).get("usage") or {}
        msgs.append({"role": m.get("role"), "content": c[:max_chars], "truncated": len(c) > max_chars,
                     "chars": len(c), "usage": {k: u.get(k) for k in ("prompt_tokens", "completion_tokens")} if u else None,
                     "exit_status": (m.get("extra") or {}).get("exit_status")})
    return {"messages": msgs, "info": {"api_calls": ((t.get("info") or {}).get("model_stats") or {}).get("api_calls")}}


# ---------------------------------------------------------------- API: live runs

JOBS: dict = {}


class LiveReq(BaseModel):
    cid: str
    kind: str                      # none | mem0 | kv
    run_id: str = "demo_live"
    api_base: str | None = None    # local provider only; default = RSM_API_BASE or the sbatch endpoint file
    served_name: str = "coder-next"
    step_limit: int = 100
    memory_system: str | None = None
    provider: str = "local"        # local | goodvision-test | goodvision-prod (demo/providers.py)
    model: str | None = None       # provider model id (GoodVision: market id or "auto"); local: served_name


def _endpoint_ok(api_base):
    try:
        with urllib.request.urlopen(api_base.rstrip("/") + "/models", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def _tail(path, n=200):
    try:
        lines = open(path, errors="replace").read().split("\n")
        return "\n".join(lines[-n:])
    except Exception:
        return ""


@app.post("/api/live/start")
def live_start(req: LiveReq):
    return _start_live(req)


def _start_live(req):
    if os.environ.get("DEMO_DISABLE_LIVE") == "1":
        raise HTTPException(403, "live runs are disabled on this deployment (DEMO_DISABLE_LIVE=1)")
    if req.cid not in CHAINS:
        raise HTTPException(400, "unknown chain")
    if req.kind not in LIVE_SYSTEMS:
        raise HTTPException(400, "kind must be none|mem0|kv")
    if not _sif_ok(req.cid):
        raise HTTPException(400, f"SIF missing: {_sif_path(req.cid)} (apptainer pull docker://{CHAINS[req.cid]['docker_image']})")
    model = req.model or (req.served_name if req.provider == "local" else PV.PROVIDERS.get(req.provider, {}).get("default_model"))
    penv, api_base = _provider_env(req.provider, model, api_base=req.api_base)
    system = req.memory_system or LIVE_SYSTEMS[req.kind]
    if req.kind == "none":
        system = None
    arm = f"mem_{system}" if system else "seq"
    job = uuid.uuid4().hex[:8]
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    chains_file = LIVE_DIR / f"{job}.chains.txt"
    chains_file.write_text(req.cid + "\n")
    log = LIVE_DIR / f"{job}.log"
    out_dir = RUNS / req.run_id / f"{req.cid}__s0__{arm}"
    if out_dir.exists():
        shutil.rmtree(out_dir)      # a fresh demo run; the harness would otherwise skip a complete chain
    env = dict(os.environ)
    env.pop("RSM_ACTOR_NOTHINK", None)
    env.update(penv)                       # endpoint, keys, model names, strict/no-think switches (demo/providers.py)
    env.update({
        "RSM_LEDGER": "1", "RSM_SIF_DIR": str(SIF_DIR), "RSM_RUNS_DIR": str(RUNS),
        "RSM_HF_HOME": env.get("RSM_HF_HOME", env.get("HF_HOME", str(LIVE_DIR / "hf_home"))),
        "PYTHONUNBUFFERED": "1",
    })
    cmd = [sys.executable, str(ROOT / "harness/mini_swe_agent.py"),
           *(["--memory-system", system] if system else ["--arm", "seq"]),
           "--chains-file", str(chains_file), "--run-id", req.run_id,
           "--step-limit", str(req.step_limit), "--served-name", model,
           "--endpoint-file", str(LIVE_DIR / "unused_endpoint")]
    lf = open(log, "w")
    lf.write("$ " + " ".join(cmd) + "\n\n"); lf.flush()
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT)
    JOBS[job] = {"id": job, "cid": req.cid, "kind": req.kind, "arm": arm, "run_id": req.run_id,
                 "started": time.time(), "status": "running", "returncode": None, "log": str(log),
                 "api_base": api_base, "provider": req.provider, "model": model, "pid": p.pid}

    def _wait():
        rc = p.wait()
        lf.close()
        scrub.scrub_dir(out_dir, [env.get("RSM_API_KEY", ""), env.get("GOODVISION_API_KEY", "")])
        JOBS[job].update(status="done" if rc == 0 else f"exit {rc}", returncode=rc, finished=time.time())
    threading.Thread(target=_wait, daemon=True).start()
    return JOBS[job]


@app.get("/api/live")
def live_list():
    return sorted(JOBS.values(), key=lambda j: j["started"], reverse=True)


@app.get("/api/live/{job}")
def live_get(job: str, tail: int = 200):
    if job not in JOBS:
        raise HTTPException(404, "no such job")
    j = dict(JOBS[job])
    j["log_tail"] = _tail(j["log"], tail)
    d = RUNS / j["run_id"] / f"{j['cid']}__s0__{j['arm']}"
    j["results"] = _read_jsonl(d / "results.jsonl") if (d / "results.jsonl").exists() else []
    j["n_calls"] = sum(1 for _ in open(d / "calls.jsonl")) if (d / "calls.jsonl").exists() else 0
    return j


@app.post("/api/live/{job}/stop")
def live_stop(job: str):
    if job not in JOBS:
        raise HTTPException(404, "no such job")
    try:
        os.kill(JOBS[job]["pid"], 15)
    except ProcessLookupError:
        pass
    return {"ok": True}


@app.get("/api/endpoint")
def endpoint(api_base: str):
    return {"api_base": api_base, "ok": _endpoint_ok(api_base)}


# ---------------------------------------------------------------- API: one session as a chat

import re

_PATH_RE = re.compile(r"(?<![\w/@])((?:[\w.\-]+/)*[\w\-][\w.\-]*\.(?:py|pyi|pyx|md|rst|txt|toml|cfg|ini|yaml|yml|json|html|js|ts|css|c|h|cpp|sh|csv|xml|in|lock|conf))\b")
_EDIT_RE = re.compile(r"\b(sed\s+-i|tee\b|>\s*[\w./\-]+|>>|cat\s*>|python\s+-\s*<<|str_replace|git\s+apply|patch\b|mv\b|rm\b|cp\b|echo\s.*>)")
_SKIP_PREFIX = ("/tmp", "/opt", "/usr", "/etc", "/proc", "/dev", "/root", "/home")


def _parse_turns(msgs):
    """mini-swe-agent messages -> [{step, thought, command, raw, usage, observation}]."""
    turns, cur = [], None
    for m in msgs:
        role, c = m.get("role"), m.get("content") or ""
        if role == "assistant":
            u = (((m.get("extra") or {}).get("response") or {}) or {}).get("usage") or {}
            th = re.search(r"THOUGHT:\s*([\s\S]*?)(?:\n```|$)", c)
            cmd = re.search(r"```mswea_bash_command\s*\n([\s\S]*?)```", c)
            resp = ((m.get("extra") or {}).get("response") or {}) or {}
            cur = {"step": len(turns) + 1, "thought": (th.group(1).strip() if th else c.strip()),
                   "command": (cmd.group(1).strip() if cmd else ""), "raw": c, "model": resp.get("model"),
                   "usage": ({"prompt": u.get("prompt_tokens"), "completion": u.get("completion_tokens")} if u else None),
                   "observation": None}
            turns.append(cur)
        elif role == "user" and cur is not None and cur["observation"] is None:
            rc = re.search(r"<returncode>(-?\d+)</returncode>", c)
            out = re.search(r"<output>\n?([\s\S]*?)</output>\s*$", c)
            body = out.group(1) if out else c
            cur["observation"] = {"returncode": int(rc.group(1)) if rc else None, "output": body[:20000],
                                  "chars": len(body), "truncated": len(body) > 20000}
        elif role == "exit":
            if cur is not None:
                cur["exit"] = (m.get("extra") or {}).get("exit_status") or "exit"
    return turns


def _split_first_user(c):
    """-> (memory_block, pr_description, instructions)."""
    mem = ""
    if MEM_HEAD in c:
        mem = c.split(MEM_HEAD, 1)[1].split(MEM_TAIL, 1)[0].strip()
        head = "Notes from earlier work sessions on this repository:"
        if mem.startswith(head):
            mem = mem[len(head):].strip()
        c = c.split(MEM_HEAD, 1)[0] + c.split(MEM_TAIL, 1)[1] if MEM_TAIL in c else c
    pr = re.search(r"<pr_description>\s*(?:Consider the following PR description:\s*)?([\s\S]*?)</pr_description>", c)
    ins = re.search(r"<instructions>([\s\S]*?)</instructions>", c)
    return mem, (pr.group(1).strip() if pr else c.strip()), (ins.group(1).strip() if ins else "")


def _parse_diff(text):
    files = []
    for chunk in re.split(r"(?=^diff --git )", text or "", flags=re.M):
        if not chunk.startswith("diff --git"):
            continue
        m = re.match(r"diff --git a/(\S+) b/(\S+)", chunk)
        path = (m.group(2) if m else "?")
        add = sum(1 for l in chunk.splitlines() if l.startswith("+") and not l.startswith("+++"))
        rem = sum(1 for l in chunk.splitlines() if l.startswith("-") and not l.startswith("---"))
        files.append({"path": path, "added": add, "removed": rem, "diff": chunk})
    return files


def _mentioned_files(turns, repo_root, edited):
    seen = collections.OrderedDict()
    for t in turns:
        texts = [(t["command"], True)]
        if t["observation"]:
            texts.append((t["observation"]["output"][:6000], False))
        for txt, is_cmd in texts:
            for p in _PATH_RE.findall(txt):
                q = p
                if repo_root and q.startswith(repo_root.strip("/") + "/"):
                    q = q[len(repo_root.strip("/")) + 1:]
                while q.startswith("./"):
                    q = q[2:]
                if q[:2] in ("a/", "b/") and q.count("/") >= 1:      # git-diff prefixes
                    q = q[2:]
                if q.startswith(_SKIP_PREFIX) or "site-packages" in q or q.count("/") > 8 or len(q) < 4:
                    continue
                e = seen.setdefault(q, {"path": q, "mentions": 0, "in_commands": 0, "edited": q in edited, "first_step": t["step"]})
                e["mentions"] += 1
                e["in_commands"] += 1 if is_cmd else 0
    out = sorted(seen.values(), key=lambda e: (not e["edited"], -e["in_commands"], -e["mentions"]))
    return out[:60]


@app.get("/api/session")
def session(run: str, arm: str, cid: str, order: int):
    if cid not in CHAINS:
        raise HTTPException(404, "unknown chain")
    d = RUNS / run / f"{cid}__s0__{arm}"
    traj = _load_traj(d, order)
    if not traj:
        raise HTTPException(404, f"no trajectory for task {order} of {cid} in {run}/{arm}")
    kind = next((k for k, a in ARMS.items() if a == arm), "kv" if arm.startswith("mem_") else "none")
    x = load_chain_arm(run, arm, cid, kind) or {"sessions": []}
    srow = next((s for s in x["sessions"] if s["order"] == order), None)
    rrow = next((r for r in _read_jsonl(d / "results.jsonl") if r.get("order") == order), {})
    msgs = traj.get("messages", [])
    system = msgs[0]["content"] if msgs and msgs[0].get("role") == "system" else ""
    mem, pr, ins = _split_first_user(msgs[1]["content"] if len(msgs) > 1 else "")
    turns = _parse_turns(msgs)
    info = traj.get("info") or {}
    submission = info.get("submission") or next((m.get("content") for m in msgs if m.get("role") == "exit"), "") or ""
    changes = _parse_diff(submission)
    repo_root = ((info.get("config") or {}).get("environment") or {}).get("cwd") or ""
    files = _mentioned_files(turns, repo_root, {f["path"] for f in changes})
    calls = _read_calls(d).get(order) or []
    ledger = [{"idx": r.get("call_idx"), "role": r.get("role"), "prompt": r.get("prompt_tokens"), "cached": r.get("cached_tokens"),
               "completion": r.get("completion_tokens"), "cached_src": r.get("cached_src"), "latency_s": r.get("latency_s"),
               "finish": r.get("finish_reason")} for r in calls]
    task = next((t for t in CHAINS[cid]["tasks"] if t["order"] == order), {})
    note = (srow or {}).get("note_written", "")
    if not note and (d / "store.json").exists():          # custom projects persist the store: show the note before any later task injects it
        try:
            note = next((payload for name, payload in json.load(open(d / "store.json")) if name == task.get("instance")), "")
        except Exception:
            note = ""
    return {
        "run": run, "arm": arm, "kind": kind, "cid": cid, "order": order, "instance": task.get("instance"),
        "repo": CHAINS[cid]["repo"], "repo_root": repo_root, "sif": _sif_ok(cid),
        "model": ((info.get("config") or {}).get("model") or {}).get("model_name"),
        "system_prompt": system, "memory": mem, "problem_statement": pr or task.get("problem_statement", ""), "instructions": ins,
        "turns": turns, "exit_status": info.get("exit_status") or rrow.get("exit"), "submission": submission, "changes": changes,
        "files": files, "result": {k: rrow.get(k) for k in ("resolved", "gradable", "exit", "error", "f2p", "p2p_failed", "p2p_missing", "n_steps", "secs", "injected_chars", "mem_nodes", "mem_write_secs", "model", "sandbox", "custom", "delta_chars")},
        "note_written": note, "tok": (srow or {}).get("tok"), "ledger": ledger,
    }


_REPO_DIR = {}


def _image_repo_dir(cid):
    if cid not in _REPO_DIR:
        r = subprocess.run(["apptainer", "exec", str(_sif_path(cid)), "bash", "-c", "ls -d /*/.git 2>/dev/null"],
                           capture_output=True, text=True, timeout=60)
        dirs = [l.rsplit("/.git", 1)[0] for l in r.stdout.split()]
        _REPO_DIR[cid] = dirs[0] if len(dirs) == 1 else "/testbed"
    return _REPO_DIR[cid]


@app.get("/api/file")
def file(cid: str, path: str, max_bytes: int = 200_000):
    """A repository file as it is in the task image (= the chain's base commit)."""
    if cid not in CHAINS:
        raise HTTPException(404, "unknown chain")
    if not _sif_ok(cid):
        raise HTTPException(404, "image not available for this chain")
    if ".." in path or path.startswith("/"):
        raise HTTPException(400, "bad path")
    root = _image_repo_dir(cid)
    r = subprocess.run(["apptainer", "exec", str(_sif_path(cid)), "bash", "-c", f"head -c {int(max_bytes)} {root}/{path}"],
                       capture_output=True, text=True, errors="replace", timeout=60)
    if r.returncode != 0:
        raise HTTPException(404, (r.stderr or "not found").strip()[-200:])
    return {"cid": cid, "path": path, "root": root, "content": r.stdout, "truncated": len(r.stdout) >= max_bytes,
            "source": "task image (chain base commit)"}


class AskReq(BaseModel):
    run: str
    arm: str
    cid: str
    order: int
    question: str
    api_base: str | None = None
    served_name: str = "coder-next"
    provider: str = "local"
    model: str | None = None


@app.post("/api/ask")
def ask(req: AskReq):
    """Answer a question about a session with the selected model endpoint (the demo's chat box)."""
    api, key, strict, model = _resolve_endpoint(req.provider, req.model or (req.served_name if req.provider == "local" else None))
    s = session(req.run, req.arm, req.cid, req.order)
    parts = [f"Repository: {s['repo']}  Task: {s['instance']}  Memory system: {ARMS_LABEL.get(s['kind'], s['arm'])}",
             f"ISSUE:\n{s['problem_statement'][:4000]}"]
    if s["memory"]:
        parts.append(f"MEMORY INJECTED BEFORE THIS SESSION:\n{s['memory'][:4000]}")
    for t in s["turns"]:
        o = t["observation"] or {}
        parts.append(f"STEP {t['step']}\nTHOUGHT: {t['thought'][:500]}\n$ {t['command'][:300]}\n-> rc={o.get('returncode')} {(o.get('output') or '')[:500]}")
    parts.append(f"FINAL PATCH:\n{s['submission'][:3000]}")
    parts.append(f"GRADING: resolved={s['result'].get('resolved')} f2p={json.dumps(s['result'].get('f2p'))[:800]} exit={s['exit_status']}")
    if s["note_written"]:
        parts.append(f"MEMORY WRITTEN AFTER THIS SESSION:\n{s['note_written'][:2000]}")
    ctx = "\n\n".join(parts)[:90000]
    body = {"model": model, "messages": [{"role": "system", "content": "You are reviewing a recorded coding-agent session on a ChainSWE task. Answer the user's question concretely from the transcript below; cite step numbers and file paths; say so when the transcript does not contain the answer."},
                                         {"role": "user", "content": f"{ctx}\n\nQUESTION: {req.question}"}]}
    if strict:
        body["max_completion_tokens"] = 700          # OpenAI-protocol gateway: no vLLM-only params, no forced temperature
    else:
        body.update({"temperature": 0.2, "max_tokens": 700, "chat_template_kwargs": {"enable_thinking": False}})
    t0 = time.time()
    j = _chat_completion(api, key, body)
    return {"answer": j["choices"][0]["message"]["content"].split("</think>")[-1].strip(), "usage": j.get("usage"), "model": j.get("model"),
            "latency_s": round(time.time() - t0, 2), "request_id": j.get("_request_id")}


ARMS_LABEL = {"none": "no memory", "mem0": "Mem0", "kv": "Knowledge Vault"}


# ---------------------------------------------------------------- comparison configurations (memory × model endpoint)

def _configs():
    try:
        return json.load(open(CONFIGS_FILE))
    except Exception:
        return {"configs": [], "default_pair": []}


def config_run_id(provider, model):
    return "cfg__" + re.sub(r"[^A-Za-z0-9.]+", "-", f"{provider}__{model}").strip("-")


@app.get("/api/configs")
def configs():
    """Presets from demo/configs.json with their run ids, provider readiness and which chains have sessions."""
    cfg = _configs(); runs = set(_runs()); ready = {}
    out = []
    for c in cfg["configs"]:
        rid = c.get("run_id") or config_run_id(c["provider"], c["model"])
        if c["provider"] not in ready:
            pc = PV.check(c["provider"], api_base=_default_api_base() if c["provider"] == "local" else None)
            ready[c["provider"]] = (pc["ok"], pc.get("code") or pc.get("message"))
        chains = []
        if rid in runs:
            arm = ARMS[c["kind"]]
            chains = sorted(p.name.split("__")[0] for p in (RUNS / rid).glob(f"*__s0__{arm}") if (p / "results.jsonl").exists())
        out.append({**c, "run_id": rid, "arm": ARMS[c["kind"]], "provider_ok": ready[c["provider"]][0], "provider_note": ready[c["provider"]][1], "chains": chains})
    return {"configs": out, "default_pair": cfg.get("default_pair", []), "showcase": cfg.get("showcase"), "prices": {k: {kk: v[kk] for kk in ("input", "cached_input", "output", "verified")} for k, v in _model_prices().items()}}


class ConfigRunReq(BaseModel):
    cid: str
    configs: list                 # config ids from demo/configs.json, or inline {kind, provider, model}
    step_limit: int = 100


@app.post("/api/configs/run")
def configs_run(req: ConfigRunReq):
    known = {c["id"]: c for c in _configs()["configs"]}
    jobs, errors = [], []
    for c in req.configs:
        c = known.get(c) if isinstance(c, str) else c
        if not c:
            errors.append("unknown config"); continue
        if c.get("recorded"):
            errors.append(f"{c.get('label', c.get('id'))}: recorded paper run, not re-runnable from the demo"); continue
        rid = config_run_id(c["provider"], c["model"])
        try:
            j = _start_live(LiveReq(cid=req.cid, kind=c["kind"], run_id=rid, provider=c["provider"], model=c["model"], step_limit=req.step_limit))
            j["config"] = c.get("id") or c.get("short") or f"{c['kind']}+{c['model']}"
            jobs.append(j)
        except HTTPException as e:
            errors.append(f"{c.get('label', c.get('id', c['kind']))}: {e.detail}")
    return {"jobs": jobs, "errors": errors}


# ---------------------------------------------------------------- custom sandbox projects ("your own task")

CUSTOM_RUN = "custom"
PROJECTS_FILE = RUNS / CUSTOM_RUN / "projects.json"
CUSTOM_KINDS = {"kv": "mem_fusednote", "mem0": "mem_factsall", "none": "seq"}


def _projects():
    try:
        return json.load(open(PROJECTS_FILE))
    except Exception:
        return {}


def _save_projects(p):
    PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROJECTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, indent=1))
    os.replace(tmp, PROJECTS_FILE)


def _register_custom_chains():
    """Custom projects appear as chains (cid = project name) so /api/session, /api/runchains and the compare page work."""
    for name, p in _projects().items():
        CHAINS[name] = {"cid": name, "repo": p.get("label") or p["sandbox"], "source": "custom", "date_range": p.get("created"),
                        "docker_image": f"custom:{p['sandbox']}", "tag": p["sandbox"], "n_tasks": len(p["tasks"]),
                        "tasks": [{"order": t["order"], "instance": t["instance"], "title": t["problem_statement"].strip().splitlines()[0][:160],
                                   "problem_statement": t["problem_statement"]} for t in p["tasks"]], "custom": True}


_register_custom_chains()


def _sandboxes():
    """Images under SIF_DIR: chain repo images (named by docker tag) and generic sandbox-*.sif images."""
    out = []
    by_tag = {c["tag"]: c for c in CHAINS.values() if not c.get("custom")}
    if SIF_DIR.exists():
        for p in sorted(SIF_DIR.glob("*.sif")):
            tag = p.name[:-4]
            try:
                ok = p.stat().st_size >= 100_000_000
            except OSError:
                ok = False
            if not ok:
                continue
            c = by_tag.get(tag)
            if c:
                out.append({"id": tag, "kind": "repo", "label": f"{c['repo']} — repo at the chain's base commit ({c['cid']})", "chain": c["cid"]})
            elif tag.startswith("sandbox-"):
                out.append({"id": tag, "kind": "generic", "label": f"{tag.replace('sandbox-', '')} — empty workspace (/work, git initialised)"})
    return out


@app.get("/api/sandboxes")
def sandboxes():
    return _sandboxes()


@app.get("/api/custom/projects")
def custom_projects():
    ps = _projects()
    out = []
    for name, p in ps.items():
        arms = {}
        for kind, arm in CUSTOM_KINDS.items():
            x = load_chain_arm(CUSTOM_RUN, arm, name, kind)
            if x:
                arms[kind] = {"arm": arm, "orders": [s["order"] for s in x["sessions"]], "usd": round(sum(s["tok"]["usd"] for s in x["sessions"]), 4)}
        out.append({"name": name, "sandbox": p["sandbox"], "label": p.get("label"), "created": p.get("created"),
                    "tasks": [{"order": t["order"], "instance": t["instance"], "title": t["problem_statement"].strip().splitlines()[0][:120]} for t in p["tasks"]],
                    "arms": arms})
    return out


class ProjectReq(BaseModel):
    name: str
    sandbox: str


@app.post("/api/custom/project")
def custom_project(req: ProjectReq):
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", req.name.strip()).strip("-")[:48]
    if not name:
        raise HTTPException(400, "project name required")
    if name in CHAINS and not CHAINS[name].get("custom"):
        raise HTTPException(400, "name collides with a ChainSWE chain")
    sb = next((s for s in _sandboxes() if s["id"] == req.sandbox), None)
    if not sb:
        raise HTTPException(400, f"unknown or missing sandbox image {req.sandbox}")
    ps = _projects()
    if name not in ps:
        ps[name] = {"sandbox": req.sandbox, "label": sb["label"], "created": time.strftime("%Y-%m-%d %H:%M"), "tasks": []}
        _save_projects(ps)
        _register_custom_chains()
    return {"name": name, **ps[name]}


class CustomTaskReq(BaseModel):
    project: str
    task: str
    kinds: list = ["kv"]              # which arms to run this task on, e.g. ["kv", "mem0"]
    provider: str = "local"
    model: str | None = None
    step_limit: int = 60
    order: int | None = None          # rerun an existing task on another arm; default = append a new task


@app.post("/api/custom/start")
def custom_start(req: CustomTaskReq):
    if os.environ.get("DEMO_DISABLE_LIVE") == "1":
        raise HTTPException(403, "sandbox runs are disabled on this deployment (DEMO_DISABLE_LIVE=1)")
    ps = _projects()
    p = ps.get(req.project)
    if not p:
        raise HTTPException(404, "unknown project")
    for k in req.kinds:
        if k not in CUSTOM_KINDS:
            raise HTTPException(400, f"kind must be one of {sorted(CUSTOM_KINDS)}")
    if req.order is None:
        text = req.task.strip()
        if not text:
            raise HTTPException(400, "task text required")
        order = len(p["tasks"]) + 1
        inst = f"{req.project}-{order}-{re.sub(r'[^A-Za-z0-9]+', '-', text.splitlines()[0]).strip('-').lower()[:32] or 'task'}"
        p["tasks"].append({"order": order, "instance": inst, "problem_statement": text, "created": time.strftime("%Y-%m-%d %H:%M")})
        _save_projects(ps)
        _register_custom_chains()
    else:
        order = req.order
        if not any(t["order"] == order for t in p["tasks"]):
            raise HTTPException(400, f"project has no task {order}")
    model = req.model or (os.environ.get("DEMO_SERVED_NAME", "coder-next") if req.provider == "local" else PV.PROVIDERS.get(req.provider, {}).get("default_model"))
    penv, api_base = _provider_env(req.provider, model)
    jobs = []
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    for kind in req.kinds:
        arm = CUSTOM_KINDS[kind]
        job = uuid.uuid4().hex[:8]
        log = LIVE_DIR / f"{job}.log"
        env = dict(os.environ)
        env.pop("RSM_ACTOR_NOTHINK", None)
        env.update(penv)
        env.update({"RSM_LEDGER": "1", "RSM_SIF_DIR": str(SIF_DIR), "RSM_RUNS_DIR": str(RUNS),
                    "RSM_HF_HOME": env.get("RSM_HF_HOME", env.get("HF_HOME", str(LIVE_DIR / "hf_home"))), "PYTHONUNBUFFERED": "1"})
        cmd = [sys.executable, str(HERE / "custom_runner.py"), "--project", req.project, "--order", str(order), "--kind", kind,
               "--sandbox", p["sandbox"], "--step-limit", str(req.step_limit), "--served-name", model]
        lf = open(log, "w")
        lf.write("$ " + " ".join(cmd) + "\n\n"); lf.flush()
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT)
        JOBS[job] = {"id": job, "cid": req.project, "kind": kind, "arm": arm, "run_id": CUSTOM_RUN, "order": order, "custom": True,
                     "started": time.time(), "status": "running", "returncode": None, "log": str(log), "api_base": api_base,
                     "provider": req.provider, "model": model, "pid": proc.pid}

        def _wait(job=job, proc=proc, lf=lf, arm=arm, env=env):
            rc = proc.wait()
            lf.close()
            scrub.scrub_dir(RUNS / CUSTOM_RUN / f"{req.project}__s0__{arm}", [env.get("RSM_API_KEY", ""), env.get("GOODVISION_API_KEY", "")])
            JOBS[job].update(status="done" if rc == 0 else f"exit {rc}", returncode=rc, finished=time.time())
        threading.Thread(target=_wait, daemon=True).start()
        jobs.append(JOBS[job])
    return {"order": order, "jobs": jobs}


# ---------------------------------------------------------------- static

@app.get("/")
def index():
    return FileResponse(HERE / "static/index.html")


@app.get("/report")
def report():
    return FileResponse(HERE / "static/report.html")


@app.get("/compare")
def compare():
    return FileResponse(HERE / "static/compare.html")


app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("DEMO_HOST", "127.0.0.1"), port=int(os.environ.get("DEMO_PORT", "8765")))
