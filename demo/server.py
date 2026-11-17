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
import contextlib
import errno
import glob
import ipaddress
import json
import os
import pathlib
import re
import shutil
import socket
import stat
import statistics as st
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import functools
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

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
# Further memory systems of the paper run v3main100 (every chain has them), shown by the compare view next to ARMS.
# The agent view and /api/summary keep using ARMS only; live runs (LIVE_SYSTEMS) and sandbox tasks (CUSTOM_KINDS) take memop too.
#     memop  -> mem_memop    MemOp: one guidance document, rewritten by a writer call after every task (also runs live)
#     rawcat -> mem_rawcat   full history: earlier sessions pasted in verbatim (no writer model; recorded only)
EXTRA_ARMS = {"memop": "mem_memop", "rawcat": "mem_rawcat"}
ALL_ARMS = {**ARMS, **EXTRA_ARMS}
# Memory systems a live chain run accepts (kind -> harness --memory-system; None = the seq arm, no memory). Their arm
# directories are mem_<system> / seq, i.e. ALL_ARMS[kind] for the default systems. LIVE_KINDS = the same kinds in the
# order the pages show them (ours first, no memory last), served as /api/meta live_kinds so the pages don't hard-code it.
LIVE_SYSTEMS = {"none": None, "mem0": "factsall", "kv": "fusednote", "memop": "memop"}
LIVE_KINDS = ["kv", "mem0", "memop", "none"]
assert set(LIVE_KINDS) == set(LIVE_SYSTEMS)
KV_SYSTEMS = ["fusednote", "v5knote", "v5inline", "v4knote"]
# Knowledge Vault versions the settings panel offers; the first is the default (every recorded paper run uses it).
# note: one plain sentence, capitalised, no trailing period: the pages append ". Used for new live, compare and sandbox runs; …"
KV_VERSIONS = [
    {"id": "fusednote", "label": "v3 fused note (paper runs)", "note": "After each turn, a separate writer call distils the session into a short structured note for the next turn — the version in the paper"},
    {"id": "v5knote", "label": "v5 note", "note": "A newer note template, still written by a separate writer call"},
    {"id": "v5inline", "label": "v5 inline", "note": "The agent writes the note itself as the last step of the turn, so there is no separate writer call (the writer is used if that step fails)"},
    {"id": "v4knote", "label": "v4 note", "note": "An older note template, written by a separate writer call"},
]


def _kv_pref(kv):
    return kv if kv in KV_SYSTEMS else KV_SYSTEMS[0]


def _kind_of_arm(arm):
    """Memory kind of an arm directory — the one place that decides it: the recorded-only arms (mem_memop is MemOp,
    never a Knowledge Vault version), the ARMS table, then the naming convention (mem_*facts* = Mem0, any other
    mem_* = a Knowledge Vault version, seq = none)."""
    k = next((k for k, a in EXTRA_ARMS.items() if a == arm), None) or next((k for k, a in ARMS.items() if a == arm), None)
    return k or ("kv" if arm.startswith("mem_") and "facts" not in arm else "mem0" if "facts" in arm else "none")


def _arm_for(run, cid, kind, kv=None):
    """Arm directory shown for `kind` on chain `cid` of `run`. kv: the viewer's Knowledge Vault version (settings);
    its runs are shown where they exist, else the default fusednote runs, else any other KV version present."""
    if kind != "kv":
        return ALL_ARMS[kind]
    want = f"mem_{_kv_pref(kv)}"
    for a in dict.fromkeys([want, ARMS["kv"], *(f"mem_{x}" for x in KV_SYSTEMS)]):
        if (RUNS / run / f"{cid}__s0__{a}").is_dir():
            return a
    return want
MEM0_SYSTEMS = ["factsall"]
MEMOP_SYSTEMS = ["memop"]

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


_MP = {"mtime": None, "models": {}}


def _model_prices():
    """demo/model_prices.json, re-read only when the file changes (called once per session when a chain is loaded)."""
    try:
        m = MODEL_PRICES_FILE.stat().st_mtime_ns
        if m != _MP["mtime"]:
            _MP["models"] = json.load(open(MODEL_PRICES_FILE)).get("models", {}); _MP["mtime"] = m
        return _MP["models"]
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
app.add_middleware(GZipMiddleware, minimum_size=2048)      # runchains / session payloads are 50-250 KB of JSON


@app.middleware("http")
async def _revalidate_static(request, call_next):
    """Pages and /static files: revalidate every load (ETag -> 304), so a new page never runs against a cached older
    player.js (a module import of a missing export blanks the page)."""
    resp = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or not path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


# ---------------------------------------------------------------- task summaries (one short plain line per task)
# demo/task_summaries.json = {"<cid>": {"<order>": "<summary>"}}: a 4-10 word plain description of every ChainSWE task,
# for the pages (an issue's own first line is often a tag, a heading or an issue number). DEMO_TASK_SUMMARIES overrides
# the path. It is re-read when its mtime changes (checked at most once a second), so an edited file shows without a
# restart. A task it lacks gets the cleaned first line of its issue; a sandbox task gets the first sentence of the user's
# own prompt, cleaned the same way. Every task in CHAINS carries the result as "summary"; "title" (the raw first line)
# and "problem_statement" stay as they are.
SUMMARIES_FILE = pathlib.Path(os.environ.get("DEMO_TASK_SUMMARIES", str(HERE / "task_summaries.json")))
SUMMARY_MAX = 80                                        # chars; longer lines are cut on a word boundary and end in "…"
_SUM = {"mtime": None, "checked": 0.0, "data": {}}
_SUM_LOCK = threading.Lock()
_ISSUE_REF_RE = re.compile(r"^(?:(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?|issue|bug|gh|re)\b\s*:?\s*)?(?:#|gh-)\d+\b\s*[:.)\-–—]*\s*", re.I)
_HEADING_RE = re.compile(r"^#+(?!\d)\s*")                 # markdown heading marks, not an issue number
_LEAD_TAGS_RE = re.compile(r"^(?:\[[^\]\n]{0,40}\]\s*)+")  # [BUG] [Feature request] …
_EMPH_RE = re.compile(r"^(\*\*|__)(.+?)\1:?$")
_SENTENCE_END_RE = re.compile(r"(?<!e\.g\.)(?<!i\.e\.)(?<!etc\.)(?<=[.!?])\s+")


def _clean_line(text, limit=SUMMARY_MAX):
    """One line of an issue or prompt as a short heading: leading [tags], markdown #, **bold** marks, backticks,
    "Fix #123:"-style prefixes and trailing periods go; longer than `limit` chars -> cut on a word boundary + "…"."""
    s = (text or "").strip()
    for _ in range(4):                                  # they come in any order: "### [BUG] Fix #12: …"
        s0 = s
        s = _ISSUE_REF_RE.sub("", s)
        s = _HEADING_RE.sub("", s)
        s = _LEAD_TAGS_RE.sub("", s)
        s = _EMPH_RE.sub(r"\2", s).strip()
        if s == s0:
            break
    s = re.sub(r"\s+", " ", s.replace("`", "")).strip().rstrip(".").rstrip()
    if len(s) > limit:
        sp = s.rfind(" ", 0, limit + 1)
        s = (s[:sp] if sp > limit // 2 else s[:limit]).rstrip(" ,;:-–—(") + "…"
    return s


def _issue_summary(problem):
    """No written summary: the first line of the issue that still says something once cleaned."""
    for line in (problem or "").splitlines():
        c = _clean_line(line)
        if c:
            return c
    return ""


_LIST_MARK_RE = re.compile(r"^\s*(?:(?:step|task)\s*\d+\s*[.):-]?|\d+\s*[.)]|[-*•+])\s+", re.I)
_SENTENCE_END_I_RE = re.compile(_SENTENCE_END_RE.pattern, re.I)       # "E.g." at the start of a prompt does not end it


def _prompt_summary(prompt):
    """Sandbox task: the first sentence of the user's own prompt (its first non-empty line, up to . ! or ?), cleaned. A
    leading list marker ("1.", "Step 2:", "-") goes, and a first piece of fewer than 3 words ("v2.") takes the next one too."""
    for line in (prompt or "").splitlines():
        pieces = _SENTENCE_END_I_RE.split(_LIST_MARK_RE.sub("", line.strip()))
        s = pieces[0]
        for nxt in pieces[1:]:
            if len(s.split()) >= 3:
                break
            s += " " + nxt
        c = _clean_line(s)
        if c:
            return c
    return ""


def _refresh_summaries(force=False):
    """(Re)load SUMMARIES_FILE into CHAINS when its mtime changed. A file that is missing gives every task its
    fallback; one that does not parse (half written) keeps the previous summaries and is read again next time."""
    now = time.monotonic()
    if not force and now - _SUM["checked"] < 1.0:
        return
    with _SUM_LOCK:
        _SUM["checked"] = now
        try:
            mt = SUMMARIES_FILE.stat().st_mtime_ns
        except OSError:
            mt = None
        if mt == _SUM["mtime"] and not force:
            return
        data = {}
        if mt is not None:
            try:
                raw = json.loads(SUMMARIES_FILE.read_text(encoding="utf-8"))
                data = {str(cid): {str(o): " ".join(v.split()) for o, v in d.items() if isinstance(v, str) and v.strip()}
                        for cid, d in raw.items() if isinstance(d, dict)}
            except (OSError, ValueError, AttributeError) as e:
                print(f"task summaries: could not read {SUMMARIES_FILE} ({type(e).__name__}: {e}); keeping the previous ones", flush=True)
                return
        _SUM.update(mtime=mt, data=data)
        for c in list(CHAINS.values()):
            if c.get("custom"):
                continue
            got = data.get(c["cid"]) or {}
            for t in c["tasks"]:
                t["summary"] = got.get(str(t["order"])) or _issue_summary(t["problem_statement"])


def _task_summary(cid, order):
    """The summary of task `order` of chain / sandbox project `cid` ("" when there is no such task)."""
    c = CHAINS.get(cid)
    return next((t.get("summary") or "" for t in (c or {}).get("tasks", []) if t["order"] == order), "")


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
                       "summary": _issue_summary(b.get("problem_statement")),          # SUMMARIES_FILE's, once loaded
                       "problem_statement": b.get("problem_statement") or ""} for b in tasks],
        }
    return out


CHAINS = _load_chains()
_refresh_summaries(force=True)
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
    official extraction branches (agent + user prompt), each over material[:23000]; memop = one
    guidance rewrite (memory/baselines/memop_prompt.py: the official prompt over the material plus the
    guidance this task was given, answered with the guidance the next task got — note_written);
    rawcat (and none) have no writer model: their cost is in the actor's longer prompts."""
    if kind not in ("kv", "mem0", "memop"):
        return []
    mat = _material(traj)
    rows = []
    if kind == "kv":
        pt = L._tok_est(mat[:220000]) + L._tok_est(" " * _prompt_chars("memory/ours/prompts/fused_writer_prompt.txt"))
        rows.append({"role": "note_writer", "prompt_tokens": pt, "cached_tokens": 0,
                     "uncached_prompt_tokens": pt, "completion_tokens": L._tok_est(note_written),
                     "cached_src": "estimate"})
    elif kind == "memop":            # the ledger files this call as note_writer (its prompt is under memory/baselines/prompts)
        pt = (L._tok_est(mat[:220000]) + L._tok_est(" " * _prompt_chars("memory/baselines/prompts/memop_memory_prompt.txt"))
              + L._tok_est(_memory_block(traj)))
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


def _note_written(blocks, order, n_tasks, kind=None):
    """Store delta attributed to session `order` = what the next task saw minus what this one saw. MemOp rewrites
    its single guidance document after every task, so what it wrote is the whole next guidance."""
    if order >= n_tasks:
        return ""
    nxt, cur = blocks.get(order + 1, ""), blocks.get(order, "")
    if kind == "memop":
        return nxt.strip()
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
    kind = kind or _kind_of_arm(arm)
    try:                                            # USD is baked into the sessions, so the price row is part of the key
        key = (run, arm, cid, rf.stat().st_mtime_ns, (d / "calls.jsonl").stat().st_mtime_ns if (d / "calls.jsonl").exists() else 0,
               os.environ.get("RSM_PRICE_ROW", ""), kind)      # kind decides the writer estimate
    except OSError:
        return None
    if key in _CACHE:
        return _CACHE[key]
    n_tasks = CHAINS[cid]["n_tasks"] if cid in CHAINS else 0
    rows = {r["order"]: r for r in _read_jsonl(rf) if "order" in r}
    calls = _read_calls(d)
    trajs = {k: _load_traj(d, k) for k in rows}
    blocks = {k: _memory_block(t) for k, t in trajs.items()}
    sessions = []
    for k in sorted(rows):
        r = rows[k]
        note = _note_written(blocks, k, n_tasks, kind) if kind != "none" else ""
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
            "mem_nodes": r.get("mem_nodes"), "mem_write_secs": r.get("mem_write_secs"), "mem_write_error": r.get("mem_write_error"),
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
                # live / router runs record the serving model on every call: show their real list price; recorded paper
                # runs (local Qwen, no model on the rows) keep the selected price row as a what-if
                "priced_by": "model" if tok_rows and not unknown else "row",
                "usd_shown": usd_model if tok_rows and not unknown else s["cost_usd_priced"],
                "models": by_model, "models_unpriced": unknown,
            },
        })
    out = {"cid": cid, "arm": arm, "kind": kind, "dir": str(d),
           "complete": len(sessions) == n_tasks and all(s["resolved"] is not None or s["gradable"] is False for s in sessions),
           "sessions": sessions}
    _CACHE[key] = out
    return out


_PRICE_LOCK = threading.RLock()


def _price_locked(fn):
    """Serialize endpoints that select a price row: the row is process-global (os.environ + harness.ledger cache),
    so concurrent requests for different rows could otherwise price (and cache) sessions at the wrong row."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with _PRICE_LOCK:
            return fn(*a, **kw)
    return wrapper


def _set_price(row):
    if row:
        os.environ["RSM_PRICE_ROW"] = row
    else:
        os.environ.pop("RSM_PRICE_ROW", None)
    L._PRICE["row"] = None          # harness.ledger caches the row; reset so the choice applies
    if len(_CACHE) > 4000:          # bounded: (run, arm, chain, price) entries; a changed results file gets a new key anyway
        _CACHE.clear()


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
        "arms": ARMS, "live_systems": {"kv": KV_SYSTEMS, "mem0": MEM0_SYSTEMS, "memop": MEMOP_SYSTEMS}, "kv_versions": KV_VERSIONS,
        "live_kinds": LIVE_KINDS,                  # the memory systems live chain runs accept, in page order
        "price_rows": prices["rows"], "default_price_row": prices.get("_default_row"),
        "sif_dir": str(SIF_DIR), "runs_dir": str(RUNS),
        "chains": [{"cid": c["cid"], "repo": c["repo"], "n_tasks": c["n_tasks"], "sif": _sif_ok(c["cid"]), "custom": bool(c.get("custom"))}
                   for c in list(CHAINS.values())],
        "default_api_base": _default_api_base(),
        "default_served_name": os.environ.get("DEMO_SERVED_NAME", "coder-next"),
        "providers": [{"name": n, "label": p["label"], "has_key": bool(PV.api_key(n)), "default_model": p["default_model"],
                       "models_hint": p["models_hint"], "api_base": p["api_base"] or _default_api_base(), "strict": p["strict"],
                       "console": p.get("console"), "docs": p["docs"]} for n, p in PV.PROVIDERS.items()],
        "default_provider": os.environ.get("DEMO_PROVIDER") or ("goodvision-test" if PV.api_key("goodvision-test") else "local"),
        "default_cid": _default_cid(),             # the showcase chain (see demo/README.md)
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
    provider, model = PV.gpt_route(provider, model)
    if provider == "local":
        api = _default_api_base().rstrip("/")
        if not _endpoint_ok(api):
            raise HTTPException(503, f"no model endpoint at {api} — start demo/slurm/serve_actor.sbatch")
        return api, None, False, model or os.environ.get("DEMO_SERVED_NAME", "coder-next")
    c = _provider_check(provider)
    if not c["ok"]:
        c = _provider_check(provider, fresh=True)
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


PROVIDER_TTL = float(os.environ.get("DEMO_PROVIDER_TTL", "300"))
_PROV_CACHE = {}                                   # (name, api_base) -> (checked_at, result)
_PROV_LOCKS = collections.defaultdict(threading.Lock)


def _provider_check(name, api_base=None, fresh=False):
    """PV.check behind a stale-while-revalidate cache. A check of a strict provider sends two tiny chat completions
    (one per model family) through the router, i.e. it spends credit and takes seconds; without the cache every page
    load did it. Within DEMO_PROVIDER_TTL the cached result is returned; after it, the stale result is still returned
    at once while one background probe refreshes it. Only the first check of a key (or fresh=True) waits."""
    key = (name, api_base)
    hit = _PROV_CACHE.get(key)
    if hit and not fresh:
        if time.time() - hit[0] >= PROVIDER_TTL and _PROV_LOCKS[key].acquire(blocking=False):
            def refresh():
                try:
                    _probe(name, api_base)
                finally:
                    _PROV_LOCKS[key].release()
            threading.Thread(target=refresh, daemon=True, name=f"provcheck-{name}").start()
        return hit[1]
    with _PROV_LOCKS[key]:                         # one probe per key at a time; concurrent first callers reuse it
        hit = _PROV_CACHE.get(key)
        if hit and not fresh and time.time() - hit[0] < PROVIDER_TTL:
            return hit[1]
        return _probe(name, api_base)


def _probe(name, api_base):
    c = _with_working_default(PV.check(name, api_base=api_base))
    c["checked_at"] = time.time()
    _PROV_CACHE[(name, api_base)] = (c["checked_at"], c)
    return c


def _with_working_default(c):
    """If the provider's default model failed its probe (its own probe, or its family's), default to a model that
    answered instead — same family first — so the UI does not preselect an id the router refuses."""
    fam = c.get("families") or {}
    dm = c.get("default_model") or ""
    dp = c.get("default_probe")
    bad = dp if dp and not dp["ok"] else next((f for f in fam.values() if not f["ok"] and dm.split("-")[0] == f["model"].split("-")[0]), None)
    good = sorted((f for f in fam.values() if f["ok"]), key=lambda f: f["model"].split("-")[0] != dm.split("-")[0])
    if bad and good:
        c = {**c, "default_model": good[0]["model"], "default_model_note": f"{dm} is refused ({bad['code']}: {bad.get('message') or ''}); defaulting to {good[0]['model']}"}
    return c


_MODEL_OK = {}                                     # (provider, api_base, model) -> (checked_at, probe)


def _model_usable(name, model, api_base=None):
    """Probe the exact model a run is about to use (cached PROVIDER_TTL; failures are re-probed every time). A model
    the routing policy refuses fails every call with a 400 that mini-swe-agent retries 10x with backoff (~4 min per
    task) before the task ends in error:BadRequestError — refusing to start is cheaper and says why."""
    key = (name, api_base, model)
    hit = _MODEL_OK.get(key)
    if hit and hit[1]["ok"] and time.time() - hit[0] < PROVIDER_TTL:
        return hit[1]
    c = _PROV_CACHE.get((name, api_base))
    for f in ((c[1].get("families") or {}).values() if c and time.time() - c[0] < PROVIDER_TTL else []):
        if f["model"] == model and f["ok"]:
            return f
    r = PV.probe_model(name, model, api_base=api_base)
    _MODEL_OK[key] = (time.time(), r)
    return r


_PROV_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="provcheck")


@app.get("/api/providers")
def providers_status(fresh: bool = False):
    """Status of every endpoint the demo can run against (cached for DEMO_PROVIDER_TTL s; ?fresh=1 re-probes;
    providers are probed in parallel; never raises)."""
    names = list(PV.PROVIDERS)
    return list(_PROV_POOL.map(lambda n: _provider_check(n, api_base=_default_api_base() if n == "local" else None, fresh=fresh), names))


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
    c = _provider_check(name, api_base=api_base)
    if not c["ok"]:                                 # cached failure may be stale: probe again before refusing
        c = _provider_check(name, api_base=api_base, fresh=True)
    if not c["ok"]:
        raise HTTPException(400, f"{c['label']}: {c['code']} — {c['message']}")
    if PV.PROVIDERS[name].get("market") and c["models"] and model not in c["models"] and model != "auto":
        raise HTTPException(400, f"model '{model}' is not in the {c['label']} model market ({len(c['models'])} ids; pick one from /api/providers)")
    if c["strict"] and model and model != "auto":
        r = _model_usable(name, model, api_base=api_base)
        if not r["ok"]:
            works = [f["model"] for f in (c.get("families") or {}).values() if f["ok"]]
            raise HTTPException(400, f"{c['label']} refuses model '{model}': {r['code']} — {r['message']}"
                                     + (f". Models that answer now: {', '.join(works)}" if works else ""))
    env = {k: v for k, v in V3_ENV.items() if k != "RSM_ACTOR_NOTHINK"}     # vLLM-only switch
    env.update(PV.env_for_harness(name, model, api_base=api_base))
    return env, c["api_base"]


# ---------------------------------------------------------------- API: summary

@app.get("/api/summary")
@_price_locked
def summary(run: str = DEFAULT_RUN, price: str | None = None):
    if run not in _runs():
        if run == CUSTOM_RUN:                       # no sandbox task has finished yet: an empty run, not an error
            return {"run": run, "price_row": None, "price": None, "common_chains": 0, "arms": {k: None for k in ARMS}, "chains": [], "coverage": {k: 0 for k in ARMS}}
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

def _parse_kinds(kinds):
    """?kinds=kv,mem0,memop -> the requested kinds in ALL_ARMS order (none, mem0, kv, memop, rawcat); empty = ARMS."""
    ks = [k.strip() for k in (kinds or "").split(",") if k.strip()]
    bad = [k for k in ks if k not in ALL_ARMS]
    if bad:
        raise HTTPException(400, f"unknown kind(s) {', '.join(bad)}; known: {', '.join(ALL_ARMS)}")
    return [k for k in ALL_ARMS if k in ks] if ks else list(ARMS)


def _dir_started(d):
    """When a run directory's sessions started: the earliest mtime of its files, else the directory's own (0 if gone)."""
    try:
        return min((p.stat().st_mtime for p in d.iterdir()), default=None) or d.stat().st_mtime
    except OSError:
        return 0


@app.get("/api/runchains")
@_price_locked
def runchains(run: str, price: str | None = None, kv: str | None = None, kinds: str | None = None, cid: str | None = None):
    """Every chain that has any arm directory in the run, with per-kind status — the left-hand session list.
    kinds: comma-separated subset of ALL_ARMS (default kv,mem0,none); cid: only that chain (empty list if it has no
    arm directory in the run)."""
    want = _parse_kinds(kinds)
    if run not in _runs():
        if run == CUSTOM_RUN:
            return {"run": run, "chains": []}
        raise HTTPException(404, f"run {run} not found")
    _set_price(price)
    out = []
    if cid is not None:
        cids = [cid] if cid in CHAINS and any((RUNS / run).glob(f"{glob.escape(cid)}__s0__*")) else []
    else:
        cids = _chains_in_run(run)
    _refresh_summaries()
    for cid in cids:
        row = {"cid": cid, "repo": CHAINS[cid]["repo"], "n_tasks": CHAINS[cid]["n_tasks"], "sif": _sif_ok(cid), "arms": {}}
        for kind in want:
            arm = _arm_for(run, cid, kind, kv)
            x = load_chain_arm(run, arm, cid, kind)
            if not x:
                continue
            row["arms"][kind] = {
                "arm": arm, "complete": x["complete"], "started": _dir_started(pathlib.Path(x["dir"])),
                "sessions": [{"order": s["order"], "instance": s["instance"], "summary": _task_summary(cid, s["order"]),
                              "resolved": s["resolved"], "n_steps": s["n_steps"],
                              "usd": s["tok"]["usd"], "usd_model": s["tok"]["usd_model"], "models": s["tok"]["models"],
                              "usd_shown": s["tok"]["usd_shown"], "priced_by": s["tok"]["priced_by"],
                              "tokens": s["tok"]["raw_total"], "injected_chars": s["injected_chars"], "exit": s["exit"]} for s in x["sessions"]],
                "usd": round(sum(s["tok"]["usd"] for s in x["sessions"]), 4),
                "usd_model": round(sum(s["tok"]["usd_model"] for s in x["sessions"]), 4),
                "usd_shown": round(sum(s["tok"]["usd_shown"] for s in x["sessions"]), 4),
                "priced_by": "model" if x["sessions"] and all(s["tok"]["priced_by"] == "model" for s in x["sessions"]) else "row",
                "resolved": sum(1 for s in x["sessions"] if s["resolved"]),
            }
        if row["arms"]:
            out.append(row)
    return {"run": run, "chains": out}


# ---------------------------------------------------------------- API: one chain

@app.get("/api/chain/{cid}")
@_price_locked
def chain(cid: str, run: str = DEFAULT_RUN, price: str | None = None, kv: str | None = None):
    if cid not in CHAINS:
        raise HTTPException(404, "unknown task")
    _set_price(price)
    _refresh_summaries()
    out = {"chain": CHAINS[cid], "run": run, "sif": _sif_ok(cid), "sif_path": str(_sif_path(cid)), "arms": {}}

    def summed(x):                                  # a copy: load_chain_arm's result is cached and shared
        return {**x, "sessions": [{**s, "summary": _task_summary(cid, s["order"])} for s in x["sessions"]]}
    if run in _runs():
        shown = set()
        for kind in ARMS:
            arm = _arm_for(run, cid, kind, kv)
            x = load_chain_arm(run, arm, cid, kind)
            if x:
                out["arms"][kind] = summed(x)
                shown.add(arm)
        # any other arm directories present for this chain (e.g. live runs with another memory system, MemOp / full history)
        for p in (RUNS / run).glob(f"{glob.escape(cid)}__s0__*"):
            arm = p.name.split("__")[-1]
            if arm not in shown:
                x = load_chain_arm(run, arm, cid, _kind_of_arm(arm))
                if x:
                    out["arms"][arm] = summed(x)
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
    kind: str                      # kv | mem0 | memop | none (LIVE_KINDS)
    run_id: str | None = None      # None -> a fresh live_<timestamp> run id (earlier live runs are kept); explicit ids are reused
    api_base: str | None = None    # local provider only; default = RSM_API_BASE or the sbatch endpoint file
    served_name: str = "coder-next"
    step_limit: int = 100
    memory_system: str | None = None   # kv: a KV_SYSTEMS version; mem0: factsall; memop: memop; None = the kind's default
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


def _live_model(req):
    """The model id a live run asks for: the request's, else the endpoint's default (local: served_name)."""
    return req.model or (req.served_name if req.provider == "local" else PV.PROVIDERS.get(req.provider, {}).get("default_model"))


def _live_duplicate(req):
    """Why a live run must not start: one of the same chain, memory system and model is already going (each costs
    $1-5 of model credit), else None. A job being stopped no longer counts. Checked by the route handlers, which the
    UI test server keeps (it swaps _start_live for a replayer)."""
    model = _live_model(req)
    for j in list(JOBS.values()):
        if (model and j.get("status") == "running" and not j.get("stopping") and not j.get("custom") and j.get("cid") == req.cid
                and j.get("kind") == req.kind and model in (j.get("model_asked"), j.get("model"))):
            return f"A live run of this task with {SHOWCASE_KINDS.get(req.kind, req.kind)} ({model}) is already running — watch it or stop it first"
    return None


_LIVE_START_LOCK = threading.Lock()      # the duplicate check and the start it guards are one step: a double click starts one run


@app.post("/api/live/start")
def live_start(req: LiveReq):
    with _LIVE_START_LOCK:
        why = _live_duplicate(req)
        if why:
            raise HTTPException(409, why)
        return _start_live(req)


LIVE_RUN_PREFIX = "live_"


def _is_live_run(run_id):
    return run_id.startswith(LIVE_RUN_PREFIX) or run_id == "demo_live"


def _new_live_run_id():
    """live_YYYYmmdd-HHMMSS, unique against existing run dirs and jobs started in the same second."""
    base = time.strftime(LIVE_RUN_PREFIX + "%Y%m%d-%H%M%S")
    taken = {j["run_id"] for j in JOBS.values()}
    rid, n = base, 1
    while rid in taken or (RUNS / rid).exists():
        n += 1
        rid = f"{base}_{n}"
    return rid


def _live_kind_error(kind):
    """Plain refusal for a memory system live runs can't use (Full history is recorded only)."""
    names = ", ".join(f"{k} ({SHOWCASE_KINDS[k]})" for k in LIVE_KINDS)
    return f"kind must be one of {names}; {SHOWCASE_KINDS.get(kind, kind)} can't run live in the demo"


def _start_live(req):
    if os.environ.get("DEMO_DISABLE_LIVE") == "1":
        raise HTTPException(403, "live runs are disabled on this deployment (DEMO_DISABLE_LIVE=1)")
    if req.cid not in CHAINS:
        raise HTTPException(400, "unknown task")
    if req.kind not in LIVE_SYSTEMS:
        raise HTTPException(400, _live_kind_error(req.kind))
    if not _sif_ok(req.cid):
        raise HTTPException(400, f"SIF missing: {_sif_path(req.cid)} (apptainer pull docker://{CHAINS[req.cid]['docker_image']})")
    asked = model = _live_model(req)
    provider, model = PV.gpt_route(req.provider, model)      # GPT ids -> Azure OpenAI when configured (saves router credit)
    penv, api_base = _provider_env(provider, model, api_base=req.api_base if provider == req.provider else None)
    if req.memory_system and req.memory_system not in {"kv": KV_SYSTEMS, "mem0": MEM0_SYSTEMS, "memop": MEMOP_SYSTEMS}.get(req.kind, []):
        raise HTTPException(400, f"unknown memory system {req.memory_system} for {req.kind}")
    system = req.memory_system or LIVE_SYSTEMS[req.kind]
    if req.kind == "none":
        system = None
    arm = f"mem_{system}" if system else "seq"
    job = uuid.uuid4().hex[:8]
    run_id = req.run_id or _new_live_run_id()
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    chains_file = LIVE_DIR / f"{job}.chains.txt"
    chains_file.write_text(req.cid + "\n")
    log = LIVE_DIR / f"{job}.log"
    out_dir = RUNS / run_id / f"{req.cid}__s0__{arm}"
    if out_dir.exists() and req.run_id:
        shutil.rmtree(out_dir)      # an explicit run id re-runs the chain in place; the harness would otherwise skip a complete chain
    env = dict(os.environ)
    env.pop("RSM_ACTOR_NOTHINK", None)
    env.update(penv)                       # endpoint, keys, model names, strict/no-think switches (demo/providers.py)
    env.update({
        "RSM_LEDGER": "1", "RSM_SIF_DIR": str(SIF_DIR), "RSM_RUNS_DIR": str(RUNS),
        "RSM_HF_HOME": env.get("RSM_HF_HOME", env.get("HF_HOME", str(LIVE_DIR / "hf_home"))),
        "RSM_LIVE_SAVE": "1",                  # harness/live_agent.py: trajectory saved after every model reply, atomically
        "RSM_MEM_SAVE": "1",                   # harness/mem_snapshot.py: memory store saved after every write (memory view)
        "PYTHONUNBUFFERED": "1",
    })
    cmd = [sys.executable, str(ROOT / "harness/mini_swe_agent.py"),
           *(["--memory-system", system] if system else ["--arm", "seq"]),
           "--chains-file", str(chains_file), "--run-id", run_id,
           "--step-limit", str(req.step_limit), "--served-name", model,
           "--endpoint-file", str(LIVE_DIR / "unused_endpoint")]
    lf = open(log, "w")
    lf.write("$ " + " ".join(cmd) + "\n\n"); lf.flush()
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)   # own group: Stop reaches the sandbox too
    JOBS[job] = {"id": job, "cid": req.cid, "kind": req.kind, "arm": arm, "run_id": run_id,
                 "started": time.time(), "status": "running", "returncode": None, "log": str(log),
                 "api_base": api_base, "provider": provider, "model": model, "model_asked": asked, "pid": p.pid}

    def _wait():
        rc = p.wait()
        lf.close()
        scrub.scrub_dir(out_dir, [env.get("RSM_API_KEY", ""), env.get("GOODVISION_API_KEY", "")])
        JOBS[job].update(status=_end_status(job, rc), returncode=rc, finished=time.time())
    threading.Thread(target=_wait, daemon=True).start()
    return JOBS[job]


@app.get("/api/live")
def live_list():
    return sorted(JOBS.values(), key=lambda j: j["started"], reverse=True)


@app.get("/api/liveruns")
@_price_locked
def live_runs(price: str | None = None):
    """Every saved live run (run ids live_* and the legacy demo_live), one entry per chain × memory system, newest
    first. The agent view lists them above the recorded run's chains; each entry opens like a recorded session."""
    _set_price(price)
    _refresh_summaries()
    out = []
    for rid in _runs():
        if not _is_live_run(rid):
            continue
        for d in sorted((RUNS / rid).glob("*__s0__*")):
            cid, arm = d.name.split("__s0__")
            if cid not in CHAINS:
                continue
            kind = _kind_of_arm(arm)
            x = load_chain_arm(rid, arm, cid, kind) or {"sessions": [], "complete": False}
            ss = x["sessions"]
            models = collections.Counter()
            for sess in ss:
                models.update({m: n for m, n in ((sess.get("tok") or {}).get("models") or {}).items() if m != "model not recorded"})
            model = models.most_common(1)[0][0] if models else None
            started = _dir_started(d)
            running = any(j["run_id"] == rid and j["cid"] == cid and j["arm"] == arm and j["status"] == "running" for j in JOBS.values())
            out.append({"run": rid, "cid": cid, "arm": arm, "kind": kind, "repo": CHAINS[cid]["repo"], "n_tasks": CHAINS[cid]["n_tasks"],
                        "model": model, "started": started, "running": running, "complete": x.get("complete", False),
                        "resolved": sum(1 for s in ss if s["resolved"]), "usd": round(sum((s["tok"] or {}).get("usd") or 0 for s in ss), 4),
                        "usd_shown": round(sum((s["tok"] or {}).get("usd_shown") or 0 for s in ss), 4),
                        "priced_by": "model" if ss and all((s["tok"] or {}).get("priced_by") == "model" for s in ss) else "row",
                        "sessions": [{k: s.get(k) for k in ("order", "instance", "resolved", "n_steps")} | {"summary": _task_summary(cid, s["order"]), "usd": (s["tok"] or {}).get("usd"), "usd_shown": (s["tok"] or {}).get("usd_shown")} for s in ss]})
    return sorted(out, key=lambda e: e["started"], reverse=True)


def _from_replaced_attempt(j, f):
    """A sandbox job (one task of a project) may rerun a task (↻): until its runner rewinds the arm and writes the new
    attempt, the old attempt's results row for that order and its traj_<order>_*.json are still on disk. A file last
    written before the job started belongs to that replaced attempt, so the live view treats it as not there yet."""
    if not j.get("custom") or not f:
        return False
    try:
        return os.path.getmtime(f) < (j.get("started") or 0)
    except OSError:
        return True


def _live_results(j, d):
    rf = d / "results.jsonl"
    rows = _read_jsonl(rf) if rf.exists() else []
    if _from_replaced_attempt(j, rf):
        rows = [r for r in rows if r.get("order") != j.get("order")]
    return rows


@app.get("/api/live/{job}")
def live_get(job: str, tail: int = 200):
    if job not in JOBS:
        raise HTTPException(404, "no such job")
    j = dict(JOBS[job])
    j["log_tail"] = _tail(j["log"], tail)
    d = RUNS / j["run_id"] / f"{j['cid']}__s0__{j['arm']}"
    j["results"] = _live_results(j, d)
    j["n_calls"] = sum(1 for _ in open(d / "calls.jsonl")) if (d / "calls.jsonl").exists() else 0
    return j


@app.get("/api/live/{job}/state")
def live_state(job: str, tail: int = 12):
    """Where a live job is right now, for the streaming transcript view (index.html / compare.html poll this).

    The harness saves the trajectory of the task in progress after every model reply (RSM_LIVE_SAVE=1), and
    appends a results.jsonl row when a task is graded, so: done = graded orders, cur = the order being worked
    on (None once the job has nothing left), has_traj = its trajectory exists (-> GET /api/session for it).
    A sandbox rerun ignores the files of the attempt it replaces until it writes its own (_from_replaced_attempt).
    """
    if job not in JOBS:
        raise HTTPException(404, "no such job")
    j = dict(JOBS[job])
    d = RUNS / j["run_id"] / f"{j['cid']}__s0__{j['arm']}"
    results = _live_results(j, d)
    if j.get("custom"):
        orders = [j["order"]]
        results = [r for r in results if r.get("order") == j["order"]]   # a sandbox job is its one task: earlier tasks are not "done" by it
    else:
        orders = [t["order"] for t in CHAINS[j["cid"]]["tasks"]] if j["cid"] in CHAINS else []
    done = sorted({r["order"] for r in results if isinstance(r.get("order"), int)})
    cur = next((o for o in orders if o not in done), None)
    traj = _traj_file(d, cur) if cur is not None else None
    if _from_replaced_attempt(j, traj):
        traj = None
    phase = "finished" if j["status"] != "running" else "starting" if cur is not None and not traj and not done else "agent" if traj else "between tasks"
    return {**j, "n_tasks": len(orders), "orders": orders, "done": done, "cur": cur, "has_traj": bool(traj), "phase": phase,
            "results": [{k: r.get(k) for k in ("order", "instance", "resolved", "exit", "n_steps", "secs", "error", "mem_nodes", "mem_write_secs")} for r in results],
            "n_calls": sum(1 for _ in open(d / "calls.jsonl")) if (d / "calls.jsonl").exists() else 0,
            "log_tail": _tail(j["log"], tail), "now": time.time()}


def _end_status(job, rc):
    return "done" if rc == 0 else ("stopped" if JOBS[job].get("stopping") else f"exit {rc}")


def _stop_job(job):
    """SIGTERM the run's whole process group (harness, sandbox commands, memory writer); SIGKILL whatever is left after 10 s."""
    j = JOBS[job]
    if j.get("status") != "running":
        return False
    j["stopping"] = True
    pid = j.get("pid") or 0
    if pid <= 0:                  # never killpg(0): that is this server's own group
        return True

    def _sig(sig):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
    _sig(signal.SIGTERM)
    threading.Timer(10, lambda: JOBS.get(job, {}).get("status") == "running" and _sig(signal.SIGKILL)).start()
    return True


@app.post("/api/live/{job}/stop")
def live_stop(job: str):
    if job not in JOBS:
        raise HTTPException(404, "no such job")
    return {"ok": True, "stopping": _stop_job(job)}


class StopReq(BaseModel):
    jobs: list = []               # job ids; empty = every running job


@app.post("/api/live/stop")
def live_stop_many(req: StopReq):
    ids = [j for j in (req.jobs or list(JOBS)) if j in JOBS]
    return {"ok": True, "stopped": [j for j in ids if _stop_job(j)]}


@app.get("/api/endpoint")
def endpoint(api_base: str):
    return {"api_base": api_base, "ok": _endpoint_ok(api_base)}


# ---------------------------------------------------------------- API: one session as a chat

import re

# a path never starts inside another path, an e-mail or a dotted / dashed name ("python3.10/inspect.py" and
# "site-packages/x.py" yield nothing rather than "10/inspect.py" / "packages/x.py"); a file name starts with a letter or
# digit ("---pytest.ini---" printed by an echo is no file), a hidden file keeps its dot (".pre-commit-config.yaml", not
# "pre-commit-config.yaml"); URLs are removed before matching (_URL_RE), so
# "docs.pytest.org/…" and "github.com/owner/repo/blob/…" never become folders of the Files tree
_PATH_RE = re.compile(r"(?<![\w/@.\-])((?:[\w.\-]+/)*\.?\w[\w.\-]*\.(?:py|pyi|pyx|md|rst|txt|toml|cfg|ini|yaml|yml|json|html|js|ts|css|c|h|cpp|sh|csv|xml|in|lock|conf))\b")
_URL_RE = re.compile(r"\b(?:[A-Za-z][A-Za-z0-9+.\-]*://|www\.)[^\s'\"<>()\[\]{}`]+")
_CUT_RE = re.compile(r"[^\n]*\n\.\.\.\[output truncated\]\.\.\.\n[^\n]*")   # the harness cuts long output mid-line: both partial lines go
_EDIT_RE = re.compile(r"\b(sed\s+-i|tee\b|>\s*[\w./\-]+|>>|cat\s*>|python\s+-\s*<<|str_replace|git\s+apply|patch\b|mv\b|rm\b|cp\b|echo\s.*>)")
_SKIP_PREFIX = ("/tmp", "/opt", "/usr", "/etc", "/proc", "/dev", "/root", "/home")
# a reply's command block = mini-swe-agent's own action_regex (the closing fence starts a line), so the command shown is
# the one that ran, also when it writes text with ``` fences in it; and the debris some models leave around the block:
# stray ``` fences, tool-call tags (</parameter>, </function>, </tool_call>, <think>…) and a THOUGHT label with nothing after it
_CMD_RE = re.compile(r"```mswea_bash_command\s*\n(.*?)\n```", re.DOTALL)
_DEBRIS_RE = re.compile(r"```[\w.+-]*|</?(?:think(?:ing)?|parameter|function|tool_call|invoke|user|assistant)\b[^>\n]*>|^[ \t]*THOUGHT\b:?", re.M)


def _prose(reply):
    """What a reply says besides its command: every ```mswea_bash_command block and the debris above removed, runs of
    blank lines closed, trimmed. '' when the reply was only a command (the transcript then shows no thought bubble)."""
    s = _DEBRIS_RE.sub("", _CMD_RE.sub("", reply))
    return re.sub(r"\n[ \t]*(?:\n[ \t]*)+\n", "\n\n", s).strip()


def _parse_turns(msgs):
    """mini-swe-agent messages -> [{step, thought, command, raw, usage, observation}]. thought = the text after
    THOUGHT:; a reply without that label (or with the command block right after it) gets its prose (_prose), so the
    thought never repeats the command."""
    turns, cur = [], None
    for m in msgs:
        role, c = m.get("role"), m.get("content") or ""
        if role == "assistant":
            u = (((m.get("extra") or {}).get("response") or {}) or {}).get("usage") or {}
            th = re.search(r"THOUGHT:\s*([\s\S]*?)(?:\n```|$)", c)
            cmd = _CMD_RE.search(c)
            resp = ((m.get("extra") or {}).get("response") or {}) or {}
            thought = th.group(1).strip() if th else ""
            if not th or "```mswea_bash_command" in thought:
                thought = _prose(c)
            cur = {"step": len(turns) + 1, "thought": thought,
                   "command": (cmd.group(1).strip() if cmd else ""), "raw": c, "model": resp.get("model"),
                   "usage": ({"prompt": u.get("prompt_tokens"), "completion": u.get("completion_tokens"),
                              "cached": ((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)} if u else None),
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
    """The files a session's commands and their output name, as repository paths: an absolute path under the repository
    (the agent's cwd, e.g. "cat /pyccel/pyccel/ast/core.py") counts as its relative path ("pyccel/ast/core.py"); a relative
    path is kept as written (it is never cut because it starts with the repository's folder name); other absolute paths,
    URLs and fragments of either are left out."""
    seen = collections.OrderedDict()
    root = (repo_root or "").rstrip("/")
    under_root = re.compile(r"(?<![\w.\-/])" + re.escape(root) + r"/(?=[\w.\-])") if root else None
    for t in turns:
        texts = [(t["command"], True)]
        if t["observation"]:
            out = t["observation"]["output"]
            out = out[:6000].rsplit("\n", 1)[0] if len(out) > 6000 else out     # a line cut in the middle could look like another path
            texts.append((_CUT_RE.sub("\n", out), False))
        for txt, is_cmd in texts:
            txt = _URL_RE.sub(" ", txt)
            if under_root:
                txt = under_root.sub(" ", txt)
            for p in _PATH_RE.findall(txt):
                q = p
                while q.startswith("./"):
                    q = q[2:]
                if q[:2] in ("a/", "b/") and q.count("/") >= 1:      # git-diff prefixes
                    q = q[2:]
                name = q.rsplit("/", 1)[-1]
                if (q.startswith(_SKIP_PREFIX) or "site-packages" in q or q.count("/") > 8 or len(q) < 4
                        or (name.count(".") >= 2 and name.rsplit(".", 1)[1] in ("c", "h"))):   # "pytest.mark.c" is a marker, not a C file
                    continue
                e = seen.setdefault(q, {"path": q, "mentions": 0, "in_commands": 0, "edited": q in edited, "first_step": t["step"]})
                e["mentions"] += 1
                e["in_commands"] += 1 if is_cmd else 0
    out = sorted(seen.values(), key=lambda e: (not e["edited"], -e["in_commands"], -e["mentions"]))
    return out[:60]


# ---------------------------------------------------------------- API: memory written / saved per session (KV vs Mem0 view)

_ITEM_HEAD = re.compile(r"^\[([^\]\s]+)\]$", re.M)


def _parse_items(block):
    """Store body as the layers render it ('[name]\ntext' items joined by blank lines, FIFO-capped from the front)
    -> ([(name, text)], truncated). A leading fragment without its header = the cap cut an item: dropped, flagged."""
    if not block or not block.strip():
        return [], False
    ms = list(_ITEM_HEAD.finditer(block))
    if not ms:
        return [("memory", block.strip())], False
    items = [(m.group(1), block[m.end():ms[i + 1].start() if i + 1 < len(ms) else len(block)].strip()) for i, m in enumerate(ms)]
    return items, bool(block[:ms[0].start()].strip())


def _saved_store(d, k):
    try:
        j = json.load(open(d / f"mem_store_{k}.json"))
        return [(n, t) for n, t in j.get("items", [])]
    except (OSError, ValueError, TypeError):
        return None


def memory_timeline(run, arm, cid, kind=None):
    """Per session: what the memory system wrote after it and the whole store it saved, plus the writer's cost.
    Exact when the harness saved mem_store_<k>.json (RSM_MEM_SAVE=1, live runs); otherwise the store after
    session k is read back from the block injected into session k+1 (the layers inject the whole store, capped
    at RSM_STORE_FIFO_CHARS), which leaves the last session's write unknown unless it stored nothing."""
    x = load_chain_arm(run, arm, cid, kind)
    if not x:
        return None
    _refresh_summaries()
    d = pathlib.Path(x["dir"]); ses = {s["order"]: s for s in x["sessions"]}
    n = CHAINS[cid]["n_tasks"] if cid in CHAINS else (max(ses) if ses else 0)
    saved = {k: v for k in ses if (v := _saved_store(d, k)) is not None}
    parse = _parse_items
    if x["kind"] == "memop":
        # MemOp injects its one guidance document as "## Task Guidance\n<text>" and saves it as the store item "guidance":
        # read the injection the same way, so a live run's snapshots and injections name the same item ("from task N")
        def parse(block):
            items, tr = _parse_items(block)
            return [("guidance", re.sub(r"^##\s*Task Guidance\s*\n", "", tx)) if nm == "memory" else (nm, tx) for nm, tx in items], tr

    def before(k):                                 # store the agent of session k started from
        if k - 1 in saved:
            return saved[k - 1], False, "saved"
        it, tr = parse(ses[k]["memory_text"]) if k in ses else ([], False)
        return it, tr, "injection"

    def after(k):                                  # store right after the write that followed session k
        if k in saved:
            return saved[k], False, "saved"
        if k + 1 in ses:
            it, tr = parse(ses[k + 1]["memory_text"])
            return it, tr, "next_injection"
        if k in ses and ses[k].get("mem_nodes") == 0:
            it, tr, _ = before(k)
            return it, tr, "unchanged"
        return None, False, "unknown"

    origin, tasks = {}, []
    for k in sorted(ses):
        s = ses[k]; t = s["tok"]
        b_items, b_tr, _ = before(k); a_items, a_tr, a_src = after(k)
        # retrieved = what the agent of session k was actually shown (its injected block), each item tagged with the task that wrote it
        r_items, r_tr = parse(s["memory_text"])
        retrieved = [{"name": nm, "text": tx, "order": origin.get(nm)} for nm, tx in r_items]
        bmap = dict(b_items)
        written = None
        if a_items is not None:
            written = [{"name": nm, "text": tx, "updated": nm in bmap} for nm, tx in a_items if bmap.get(nm) != tx]
            for w in written:
                origin.setdefault(w["name"], k)
        store = None if a_items is None else [{"name": nm, "text": tx, "order": origin.get(nm)} for nm, tx in a_items]
        tasks.append({
            "order": k, "instance": s["instance"], "summary": _task_summary(cid, k), "resolved": s["resolved"],
            "injected_chars": s["injected_chars"] or 0, "injected_items": len(r_items), "injected_truncated": r_tr,
            "retrieved": {"items": retrieved, "chars": sum(len(i["text"]) for i in retrieved), "truncated": r_tr, "injected": bool(s.get("injected"))},
            "written": {"known": written is not None, "items": written or [], "chars": sum(len(w["text"]) for w in written or []),
                        "nodes": s.get("mem_nodes"), "secs": s.get("mem_write_secs"), "error": s.get("mem_write_error"),
                        "tokens": t["writer_prompt"] + t["writer_completion"], "src": t["writer_src"],
                        "usd": round(t["usd"] - t["usd_actor"], 6)},
            "store": {"known": store is not None, "src": a_src, "items": store or [], "chars": sum(len(i["text"]) for i in store or []),
                      "truncated": a_tr},
        })
    known = [tk for tk in tasks if tk["store"]["known"]]
    last = known[-1] if known else None
    return {"run": run, "arm": arm, "kind": x["kind"], "cid": cid, "n_tasks": n, "exact": bool(saved) and len(saved) == len(ses),
            "tasks": tasks,
            "totals": {"written_items": sum(tk["written"]["nodes"] if tk["written"]["nodes"] is not None else len(tk["written"]["items"]) for tk in tasks),
                       "written_chars": sum(tk["written"]["chars"] for tk in tasks),
                       "empty_writes": sum(1 for tk in tasks if tk["written"]["nodes"] == 0),
                       "writer_tokens": sum(tk["written"]["tokens"] for tk in tasks),
                       "writer_usd": round(sum(tk["written"]["usd"] for tk in tasks), 6),
                       "writer_src": collections.Counter(tk["written"]["src"] for tk in tasks).most_common(1)[0][0] if tasks else None,
                       "store_items": len(last["store"]["items"]) if last else 0, "store_chars": last["store"]["chars"] if last else 0,
                       "store_after_order": last["order"] if last else None,
                       "injected_chars": sum(tk["injected_chars"] for tk in tasks)}}


@app.get("/api/memory")
@_price_locked
def memory(run: str, arm: str, cid: str, price: str | None = None):
    _set_price(price)
    out = memory_timeline(run, arm, cid)
    if out is None:
        raise HTTPException(404, f"no {arm} sessions for {cid} in {run}")
    return out


@app.get("/api/session")
def session(run: str, arm: str, cid: str, order: int):
    if cid not in CHAINS:
        raise HTTPException(404, "unknown task")
    _refresh_summaries()
    d = RUNS / run / f"{cid}__s0__{arm}"
    traj = _load_traj(d, order)
    if not traj:
        raise HTTPException(404, f"no trajectory for turn {order} of {cid} in {run}/{arm}")
    kind = _kind_of_arm(arm)
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
            note = next((payload for name, payload in json.load(open(d / "store.json"))
                         if name == task.get("instance") or (kind == "memop" and name == "guidance")), "")   # MemOp: its one guidance
        except Exception:
            note = ""
    return {
        "run": run, "arm": arm, "kind": kind, "cid": cid, "order": order, "instance": task.get("instance"),
        "title": task.get("title") or "", "summary": task.get("summary") or "",
        "repo": CHAINS[cid]["repo"], "repo_root": repo_root, "sif": _sif_ok(cid),
        "model": ((info.get("config") or {}).get("model") or {}).get("model_name"),
        "system_prompt": system, "memory": mem, "problem_statement": pr or task.get("problem_statement", ""), "instructions": ins,
        "turns": turns, "exit_status": info.get("exit_status") or rrow.get("exit"), "submission": submission, "changes": changes,
        "files": files, "result": {k: rrow.get(k) for k in ("resolved", "gradable", "exit", "error", "f2p", "p2p_failed", "p2p_missing", "n_steps", "secs", "injected_chars", "mem_nodes", "mem_write_secs", "model", "sandbox", "custom", "delta_chars")},
        "note_written": note, "tok": (srow or {}).get("tok"), "ledger": ledger,
    }


_REPO_DIR = {}
TREE_MAX = 20_000                  # paths returned by /api/tree (a bigger repository is cut, flagged truncated)
FILE_MAX = 2_000_000               # bytes /api/file ever returns
GIT_READ_SECS = 20                 # one git read of a workspace is killed after this (the agent can turn an object into a FIFO)
WS_WAIT_SECS = 5                   # a request waits this long for the previous read of the same workspace, then gets 503
WORK_LIST_SECS = 4                 # listing a working copy stops after this (the count is then a lower bound)
WORK_LIST_DEPTH = 64               # folders deeper than this are not listed
WORK_LIST_MAX = 200_000            # directory entries a working-copy listing looks at, at most
_TREE_CACHE = collections.OrderedDict()
_TASK_COMMIT = collections.OrderedDict()            # (workspace, HEAD sha, order) -> sha of 'rsm: task <order>' or None
_WORK_CACHE = collections.OrderedDict()             # workspace -> (monotonic time, working-copy listing), reused for 1 s
_WS_LOCKS = collections.defaultdict(threading.Lock)  # one git read per workspace at a time
_CACHE_LOCK = threading.Lock()                       # guards the caches above and _WS_LOCKS (request threads share them)
_IMG = ["apptainer", "--quiet", "exec", "--contain", "--cleanenv"]      # no host $HOME / cwd bound into the image
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _O_CLOEXEC


def _image_repo_dir(cid):
    """The repository folder inside the chain's image (per image: a project can be re-created on another image)."""
    key = str(_sif_path(cid))
    if key not in _REPO_DIR:
        r = subprocess.run([*_IMG, key, "bash", "-c", "ls -d /*/.git 2>/dev/null"], capture_output=True, text=True, timeout=60)
        dirs = [l.rsplit("/.git", 1)[0] for l in r.stdout.split()]
        _REPO_DIR[key] = dirs[0] if len(dirs) == 1 else "/testbed"
    return _REPO_DIR[key]


def _image_run(cid, script, *args, timeout=60):
    """bash -c script in the chain's task image; args arrive as $1.. (never interpolated into the script)."""
    return subprocess.run([*_IMG, str(_sif_path(cid)), "bash", "-c", script, "_", *args], capture_output=True, timeout=timeout)


def _lru_get(d, key):
    """(True, value) of an LRU cache entry, or (False, None); the caches are shared by the request threads."""
    with _CACHE_LOCK:
        if key in d:
            d.move_to_end(key)
            return True, d[key]
    return False, None


def _lru_put(d, key, value, cap):
    with _CACHE_LOCK:
        d[key] = value
        d.move_to_end(key)
        while len(d) > cap:
            d.popitem(last=False)
    return value


def _tree_cached(key, make):
    hit, v = _lru_get(_TREE_CACHE, key)
    return v if hit else _lru_put(_TREE_CACHE, key, make(), 64)


def _rel_path(path):
    """A repository-relative path from the client: no absolute path, no '..' / '.git' component, no NUL. Every other
    character is allowed (a backslash or a newline is an ordinary character of a Linux file name): the path reaches git
    only as a literal pathspec argument, the image only as a bash argument and a workspace only through openat()."""
    parts = (path or "").split("/")
    if not path or path.startswith("/") or any(p in ("..", ".git") for p in parts) or "\0" in path or len(path) > 4096:
        raise HTTPException(400, "bad path")
    rel = "/".join(p for p in parts if p not in ("", "."))
    if not rel:
        raise HTTPException(400, "bad path")
    return rel


def _custom_ws(cid, arm):
    """CUSTOM_WORK/<project>/<arm>/repo of a sandbox project (no symlink anywhere), or None."""
    if not arm or not re.fullmatch(r"seq|mem_[A-Za-z0-9_]{1,40}", arm):
        raise HTTPException(400, "arm must name one memory system of the project (seq, mem_…)")
    ws = CUSTOM_WORK / cid / arm / "repo"
    return ws if _real_dir(CUSTOM_WORK / cid) and _real_dir(ws.parent) and _real_dir(ws) else None


class _NotServed(Exception):
    """A path below a workspace that is not read: the HTTP status and the message for the client."""

    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


def _read_beneath(base, rel, max_bytes):
    """At most max_bytes of the regular file base/rel, never following a symlink below base. The agent writes that tree
    and can swap a folder for a symlink at any moment, so nothing is checked by path and then opened by path: every
    folder is opened through its parent's descriptor with O_NOFOLLOW, the file with O_NOFOLLOW | O_NONBLOCK (a FIFO cannot
    block the request), and it must be a regular file by fstat (a device such as /dev/zero is refused). Raises _NotServed."""
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise _NotServed(400, "bad path")
    fds = []
    try:
        try:
            fds.append(os.open(base, _O_DIR))
        except OSError:
            raise _NotServed(404, "not found")
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            try:
                fds.append(os.open(part, (os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _O_CLOEXEC) if last else _O_DIR, dir_fd=fds[-1]))
            except OSError as e:
                try:                    # only to word the answer: nothing has been opened through a link either way
                    link = stat.S_ISLNK(os.stat(part, dir_fd=fds[-1], follow_symlinks=False).st_mode)
                except OSError:
                    link = False
                if link or e.errno == errno.ELOOP:
                    raise _NotServed(400, "symbolic link — not followed")
                raise _NotServed(404, "not found")
        if not stat.S_ISREG(os.fstat(fds[-1]).st_mode):
            raise _NotServed(404, "not a regular file")
        out, n = [], 0
        while n < max_bytes:
            b = os.read(fds[-1], min(max_bytes - n, 1 << 20))
            if not b:
                break
            out.append(b)
            n += len(b)
        return b"".join(out)
    finally:
        for fd in fds:
            os.close(fd)


def _read_ws_file(ws, rel, max_bytes):
    """Bytes of the working file ws/rel (see _read_beneath), or the HTTP error."""
    try:
        return _read_beneath(ws, rel, max_bytes)
    except _NotServed as e:
        raise HTTPException(e.status, e.msg)


class _BorrowedGit:
    """Read-only git on a workspace repo without its own config / hooks / attributes (the agent writes them): a fresh
    bare repo in a temp dir (made on the first git call) that borrows ws/.git/objects as an alternate object store; every
    command names commits explicitly, takes pathspecs literally and is killed after GIT_READ_SECS. ok False when ws has
    no usable repository or HEAD."""

    def __init__(self, ws):
        self.ws, self.tmpd, self.bare, self.sha, self.ok = ws, None, None, None, False
        gd = ws / ".git"
        self.objs = gd / "objects"
        if not _real_dir(gd) or not _real_dir(self.objs) or os.path.lexists(self.objs / "info" / "alternates"):
            return
        self.sha = _read_head(gd)
        self.ok = bool(self.sha)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.tmpd:
            shutil.rmtree(self.tmpd, ignore_errors=True)

    def _repo(self):
        if self.bare is None:
            self.env = _git_env({"GIT_LITERAL_PATHSPECS": "1"})
            self.tmpd = pathlib.Path(tempfile.mkdtemp(dir=_incoming_dir(), prefix="tree-"))
            bare = self.tmpd / "x.git"
            subprocess.run(["git", "init", "-q", "--bare", str(bare)], env=self.env, check=True, capture_output=True, timeout=60)
            (bare / "objects" / "info").mkdir(parents=True, exist_ok=True)
            (bare / "objects" / "info" / "alternates").write_text(os.path.abspath(self.objs) + "\n")
            self.bare = bare
        return self.bare

    def git(self, *a):
        try:
            return subprocess.run(["git", "--git-dir", str(self._repo()), *a], env=self.env, capture_output=True, timeout=GIT_READ_SECS)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "reading the workspace's git history timed out")

    def blob(self, obj, max_bytes):
        """At most max_bytes of a blob; git is killed after GIT_READ_SECS (then 504)."""
        p = subprocess.Popen(["git", "--git-dir", str(self._repo()), "cat-file", "blob", obj], env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        late = threading.Event()
        timer = threading.Timer(GIT_READ_SECS, lambda: (late.set(), p.kill()))
        timer.start()
        try:
            data = p.stdout.read(max_bytes)
        finally:
            timer.cancel()
            p.kill()
            p.wait()
            p.stdout.close()
        if late.is_set():
            raise HTTPException(504, "reading the file from the workspace's git history timed out")
        return data

    def task_commit(self, order):
        """The harness commit 'rsm: task <order>' (the workspace right after that task), latest attempt, or None; cached per
        HEAD (a new task or a rerun moves HEAD, so an answer for one HEAD never changes)."""
        if order is None or not self.ok:
            return None
        key = (str(self.ws), self.sha, int(order))
        hit, c = _lru_get(_TASK_COMMIT, key)
        if hit:
            return c
        r = self.git("log", "-n", "400", "--format=%H %s", self.sha)
        c = None
        for line in r.stdout.decode(errors="replace").splitlines():
            sha, _, subj = line.partition(" ")
            if subj.strip() == f"rsm: task {int(order)}":
                c = sha
                break
        if r.returncode == 0:
            _lru_put(_TASK_COMMIT, key, c, 512)
        return c


@contextlib.contextmanager
def _ws_reader(ws):
    """_BorrowedGit of the workspace, one request at a time per workspace: a read the agent made hang (a FIFO object)
    holds one server thread for at most GIT_READ_SECS; requests behind it wait WS_WAIT_SECS, then get 503."""
    with _CACHE_LOCK:
        lock = _WS_LOCKS[str(ws)]
    if not lock.acquire(timeout=WS_WAIT_SECS):
        raise HTTPException(503, "the workspace is busy — try again in a moment")
    try:
        with _BorrowedGit(ws) as g:
            yield g
    finally:
        lock.release()


def _commit_listing(g, commit):
    """(sorted paths, at most TREE_MAX, and the total) of the files in a workspace commit."""
    r = g.git("ls-tree", "-r", "-z", "--name-only", commit)
    if r.returncode != 0:
        print(f"tree: git ls-tree failed in {g.ws}: {r.stderr.decode(errors='replace').strip()[-300:]}", flush=True)
        raise HTTPException(500, "listing the workspace's files failed (its git repository may be damaged)")
    names = [p for p in r.stdout.decode(errors="replace").split("\0") if p]
    return names[:TREE_MAX], len(names)


def _shown_name(name):
    """A file name as JSON can carry it (bytes that are not UTF-8 become U+FFFD; such a file cannot be opened)."""
    try:
        name.encode("utf-8")
        return name
    except UnicodeEncodeError:
        return os.fsencode(name).decode("utf-8", errors="replace")


def _work_listing(ws):
    """(sorted paths, at most TREE_MAX, the total, complete) of the working copy as it is now, .git left out. Only names are
    read, never contents; a symlink is listed as a file (the way git records it) and never entered: every folder is opened
    through its parent's descriptor with O_NOFOLLOW (one swapped for a link meanwhile is skipped), descriptors are held
    only along the current path. Folders deeper than WORK_LIST_DEPTH are not entered; the walk stops after WORK_LIST_MAX
    entries or WORK_LIST_SECS, checked per entry (complete False: the total is a lower bound). Reused for 1 s."""
    key, now = str(ws), time.monotonic()
    hit, v = _lru_get(_WORK_CACHE, key)
    if hit and now - v[0] < 1:
        return v[1]
    out, st_ = [], {"n": 0, "seen": 0, "complete": True}

    def walk(fd, pre, depth):
        subdirs = []
        with os.scandir(fd) as it:
            for e in it:
                st_["seen"] += 1
                if st_["seen"] > WORK_LIST_MAX or time.monotonic() - now > WORK_LIST_SECS:
                    st_["complete"] = False
                    return
                if not pre and e.name == ".git":
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        subdirs.append(e.name)
                        continue
                except OSError:
                    continue
                st_["n"] += 1
                if len(out) < TREE_MAX:
                    out.append(pre + _shown_name(e.name))
        for name in subdirs if depth < WORK_LIST_DEPTH else ():
            try:
                sub = os.open(name, _O_DIR, dir_fd=fd)
            except OSError:
                continue
            try:
                walk(sub, pre + _shown_name(name) + "/", depth + 1)
            finally:
                os.close(sub)
            if not st_["complete"]:
                return
    try:
        top = os.open(ws, _O_DIR)
    except OSError:
        return [], 0, True
    try:
        walk(top, "", 0)
    finally:
        os.close(top)
    n, complete = st_["n"], st_["complete"]
    res = (sorted(out), n, complete)
    _lru_put(_WORK_CACHE, key, (now, res), 128)
    return res


def _no_workspace(cid, arm):
    """A sandbox session whose memory system has no workspace: the reason, when the project started from an example repo
    whose image is here (the caller then shows that image: the repo before any task); otherwise the 404 is raised."""
    ran = (RUNS / CUSTOM_RUN / f"{cid}__s0__{arm}").is_dir()
    why = "this memory system's workspace is no longer on the server" if ran else "this memory system has not run in this project yet"
    if _project_source(_projects().get(cid) or {})["kind"] == "image" and _sif_ok(cid):
        return why
    raise HTTPException(404, why)


def _image_tree(cid):
    """The repository listing of the chain's task image (= the chain's base commit), cached per image."""
    root = _image_repo_dir(cid)

    def make():
        r = _image_run(cid, 'cd -- "$1" && git -c safe.directory="*" ls-files -z', root, timeout=120)
        how = "git"
        if r.returncode != 0:                      # not a git checkout: every file but .git
            r = _image_run(cid, 'cd -- "$1" && find . -name .git -prune -o \\( -type f -o -type l \\) -printf "%P\\0"', root, timeout=120)
            how = "find"
        if r.returncode != 0:
            raise HTTPException(500, f"listing the repository failed: {r.stderr.decode(errors='replace').strip()[-200:]}")
        names = sorted(p for p in r.stdout.decode(errors="replace").split("\0") if p)
        return how, names[:TREE_MAX], len(names)
    how, files, n = _tree_cached(("img", str(_sif_path(cid))), make)
    return {"cid": cid, "source": "task image (task base commit)" + ("" if how == "git" else ", every file"), "at": "base",
            "root": root, "files": files, "n": n, "truncated": n > len(files)}


@app.get("/api/tree")
def tree(cid: str, run: str | None = None, arm: str | None = None, order: int | None = None):
    """Every file of the repository the session worked in, as relative paths:
    - ChainSWE chain: the tracked files of its task image (= the chain's base commit), at "base";
    - sandbox project: the arm's workspace right after task <order> (its harness commit 'rsm: task <order>'), at "task";
      while that commit does not exist (the task is running, or it never committed) the working copy now, at "work";
      a project started from an example repo whose workspace is gone: the repo's image (before any task), at "base".
    404 when none is available (the page then builds the tree from the paths the agent mentioned)."""
    if cid not in CHAINS:
        raise HTTPException(404, "unknown task")
    if CHAINS[cid].get("custom"):
        ws = _custom_ws(cid, arm)
        if ws is None:
            gone = _no_workspace(cid, arm)
            return {**_image_tree(cid), "arm": arm, "gone": gone, "source": f"the example repository before any turn (its image) — {gone}"}
        with _ws_reader(ws) as g:
            commit = g.task_commit(order)
            if commit:
                files, n = _tree_cached(("ws", str(ws), commit), lambda: _commit_listing(g, commit))
                return {"cid": cid, "arm": arm, "source": f"the workspace right after turn {int(order)} (its git commit 'rsm: task {int(order)}')",
                        "at": "task", "commit": commit, "root": "", "files": files, "n": n, "truncated": n > len(files)}
        files, n, complete = _work_listing(ws)
        return {"cid": cid, "arm": arm, "source": "the workspace now (this turn has no commit yet: it is still running, or it stopped before committing)",
                "at": "work", "commit": None, "root": "", "files": files, "n": n, "n_partial": not complete, "truncated": not complete or n > len(files)}
    if not _sif_ok(cid):
        raise HTTPException(404, "image not available for this task")
    return _image_tree(cid)


def _file_out(data, max_bytes, **kw):
    binary = b"\0" in data[:8000]
    return {**kw, "content": "" if binary else data.decode("utf-8", errors="replace"), "binary": binary, "truncated": len(data) >= max_bytes}


def _blob_at(g, commit, rel, max_bytes):
    """content / binary / truncated of the file rel in a workspace commit, or None when it is not a file there."""
    r = g.git("ls-tree", "-z", commit, "--", rel)
    ent = r.stdout.decode(errors="replace").split("\0")[0] if r.returncode == 0 else ""
    meta, _, name = ent.partition("\t")
    if name != rel or meta.split(" ")[1:2] != ["blob"]:
        return None
    mode, _, obj = meta.split(" ")
    data = g.blob(obj, max_bytes)
    if mode == "120000":
        return {"content": f"(symbolic link to {data.decode(errors='replace')})", "binary": False, "truncated": False}
    return _file_out(data, max_bytes)


def _image_file(cid, rel, max_bytes):
    """A file of the chain's task image. Symlinks are resolved inside the image and anything that leaves the repository
    is refused; path and size are bash arguments (a trailing newline of a name survives: realpath's output ends in 'x')."""
    root = _image_repo_dir(cid)
    r = _image_run(cid, 'r=$(realpath -e -- "$1" && printf x) || exit 4; r=${r%?x}; '
                        'p=$(realpath -e -- "$1/$2" 2>/dev/null && printf x) || { echo "not found" >&2; exit 4; }; p=${p%?x}; '
                        'case "$p" in "$r"/*) ;; *) echo "the path leaves the repository" >&2; exit 3;; esac; '
                        '[ -f "$p" ] || { echo "not a regular file" >&2; exit 4; }; head -c "$3" -- "$p"', root, rel, str(max_bytes))
    if r.returncode != 0:
        raise HTTPException(400 if r.returncode == 3 else 404, (r.stderr.decode(errors="replace") or "not found").strip()[-200:])
    return _file_out(r.stdout, max_bytes, cid=cid, path=rel, root=root, source="task image (task base commit)", at="base")


@app.get("/api/file")
def file(cid: str, path: str, max_bytes: int = 200_000, arm: str | None = None, order: int | None = None):
    """A repository file, in the state /api/tree lists for the same arguments: ChainSWE chain → the task image (= the
    chain's base commit), at "base"; sandbox project → the file in the commit of task <order>, at "task", else the working
    file now, at "work" (also for a file of the workspace that is not in that commit); a project started from an example
    repo whose workspace is gone → the repo's image, at "base"."""
    if cid not in CHAINS:
        raise HTTPException(404, "unknown task")
    rel = _rel_path(path)
    max_bytes = max(1, min(int(max_bytes), FILE_MAX))
    if CHAINS[cid].get("custom"):
        ws = _custom_ws(cid, arm)
        if ws is None:
            gone = _no_workspace(cid, arm)
            return {**_image_file(cid, rel, max_bytes), "gone": gone, "source": f"the example repository before any turn (its image) — {gone}"}
        with _ws_reader(ws) as g:
            commit = g.task_commit(order)
            hit = _blob_at(g, commit, rel, max_bytes) if commit else None
        if hit:
            return {**hit, "cid": cid, "path": rel, "root": "", "at": "task", "commit": commit,
                    "source": f"the workspace right after turn {int(order)} (its git commit 'rsm: task {int(order)}')"}
        data = _read_ws_file(ws, rel, max_bytes)
        return _file_out(data, max_bytes, cid=cid, path=rel, root="", at="work", commit=None, in_task=False if commit else None,
                         source="the workspace now — this file is not in the commit of this turn" if commit else "the workspace now (this turn has no commit yet)")
    if not _sif_ok(cid):
        raise HTTPException(404, "image not available for this task")
    return _image_file(cid, rel, max_bytes)


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
    parts = [f"Repository: {s['repo']}  Turn: {s['instance']}  Memory system: {ARMS_LABEL.get(s['kind'], s['arm'])}",
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
    body = {"model": model, "messages": [{"role": "system", "content": "You are reviewing a recorded coding-agent session: one turn of a ChainSWE task (a task is a series of turns on one repository; the user calls each session a turn). Answer the user's question concretely from the transcript below; cite step numbers and file paths; say so when the transcript does not contain the answer."},
                                         {"role": "user", "content": f"{ctx}\n\nQUESTION: {req.question}"}]}
    if strict:
        body["max_completion_tokens"] = 700          # OpenAI-protocol gateway: no vLLM-only params, no forced temperature
    else:
        body.update({"temperature": 0.2, "max_tokens": 700, "chat_template_kwargs": {"enable_thinking": False}})
    t0 = time.time()
    j = _chat_completion(api, key, body)
    return {"answer": j["choices"][0]["message"]["content"].split("</think>")[-1].strip(), "usage": j.get("usage"), "model": j.get("model"),
            "latency_s": round(time.time() - t0, 2), "request_id": j.get("_request_id")}


ARMS_LABEL = {"none": "no memory", "mem0": "Mem0", "kv": "Knowledge Vault", "memop": "MemOp (one rewritten guidance document)",
              "rawcat": "full history (earlier sessions pasted in verbatim)"}


# ---------------------------------------------------------------- comparison configurations (memory × model endpoint)

def _configs():
    try:
        return json.load(open(CONFIGS_FILE))
    except Exception:
        return {"configs": [], "default_pair": []}


def config_run_id(provider, model):
    return "cfg__" + re.sub(r"[^A-Za-z0-9.]+", "-", f"{provider}__{model}").strip("-")


@app.get("/api/configs")
def configs(kv: str | None = None):
    """Presets from demo/configs.json with their run ids, provider readiness and which chains have sessions."""
    cfg = _configs(); runs = set(_runs()); ready = {}
    out = []
    for c in cfg["configs"]:
        rid = c.get("run_id") or config_run_id(c["provider"], c["model"])
        if c["provider"] not in PV.PROVIDERS:                       # a preset for an endpoint this server doesn't know
            ready[c["provider"]] = (False, "unknown endpoint (restart the server?)")
        if c["provider"] not in ready:
            pc = _provider_check(c["provider"], api_base=_default_api_base() if c["provider"] == "local" else None)
            ready[c["provider"]] = (pc["ok"], pc.get("code") or pc.get("message"))
        chains = []
        if rid in runs:
            arms = [ALL_ARMS[c["kind"]]] if c["kind"] != "kv" else [f"mem_{x}" for x in KV_SYSTEMS]
            chains = sorted({p.name.split("__")[0] for a in arms for p in (RUNS / rid).glob(f"*__s0__{a}") if (p / "results.jsonl").exists()})
        out.append({**c, "run_id": rid, "arm": ALL_ARMS.get(c["kind"]), "provider_ok": ready[c["provider"]][0], "provider_note": ready[c["provider"]][1], "chains": chains})
    return {"configs": out, "default_pair": cfg.get("default_pair", []), "showcase": cfg.get("showcase"), "prices": {k: {kk: v[kk] for kk in ("input", "cached_input", "output", "verified")} for k, v in _model_prices().items()}}


# Memory systems as the pages name them, left (ours) to right (no memory); KIND_NOTES = the one-line plain descriptions,
# lowercase fragments without a colon (the pages show them after "Name: " and "Name — ").
SHOWCASE_KINDS = {"kv": "Knowledge Vault", "mem0": "Mem0", "memop": "MemOp", "rawcat": "Full history", "none": "No memory"}
KIND_NOTES = {
    "kv": "ours — after each turn a writer distils a short note for the next turn",
    "mem0": "extracts facts from each session and passes them to the next turn",
    "memop": "keeps one guidance document, rewritten after every turn",
    "rawcat": "pastes the earlier sessions in verbatim (nothing summarised)",
    "none": "every turn starts from scratch",
}
_SHOWCASE = {}                    # (price row, run, configs.json mtime) -> {cid: {kind: stats}}


def _default_cid():
    """The example chain the pages open on: DEMO_DEFAULT_CID when it names one of configs.json's showcase chains, else its
    showcase_default, else the first showcase chain. An env value naming a chain that left the list is ignored, so a
    deployment's old setting cannot bring back a chain where Knowledge Vault does not win (see demo/README.md)."""
    cfg = _configs()
    show = [e.get("cid") for e in cfg.get("showcase_chains") or [] if isinstance(e, dict)]
    for c in (os.environ.get("DEMO_DEFAULT_CID"), cfg.get("showcase_default")):
        if c and (c in show or not show):
            return c
    return show[0] if show else "Python-Markdown_markdown_20191125"


@app.get("/api/showcase")
@_price_locked
def showcase(price: str | None = None):
    """The curated chains of demo/configs.json (showcase_chains) with whole-chain numbers of every memory system in the
    recorded run showcase_run: tokens = sum of the sessions' tok.raw_total (what /api/runchains' per-session tokens sum
    to), usd at the requested price row. Cached per price row; warmed at startup."""
    cfg = _configs()
    run = cfg.get("showcase_run") or DEFAULT_RUN
    _set_price(price)
    row = L.price_row().get("_row")
    try:
        mt = CONFIGS_FILE.stat().st_mtime_ns
    except OSError:
        mt = 0
    key = (row, run, mt)
    stats = _SHOWCASE.get(key)
    if stats is None:
        stats = {}
        in_run = set(_chains_in_run(run)) if run in _runs() else set()
        for e in cfg.get("showcase_chains") or []:
            cid = e.get("cid")
            if cid not in CHAINS or cid not in in_run:
                continue
            per = {}
            for kind in SHOWCASE_KINDS:
                x = load_chain_arm(run, ALL_ARMS[kind], cid, kind)
                if not x or not x["sessions"]:
                    continue
                ss = x["sessions"]
                per[kind] = {"tokens": sum(s["tok"]["raw_total"] for s in ss), "resolved": sum(1 for s in ss if s["resolved"]),
                             "steps": sum(s["n_steps"] or 0 for s in ss), "usd": round(sum(s["tok"]["usd"] for s in ss), 4),
                             "sessions": len(ss)}
            stats[cid] = per
        if len(_SHOWCASE) > 32:
            _SHOWCASE.clear()
        _SHOWCASE[key] = stats
    _refresh_summaries()
    chains = [{"cid": e["cid"], "repo": CHAINS[e["cid"]]["repo"], "title": e.get("title") or "", "blurb": e.get("blurb") or "",
               "n_tasks": CHAINS[e["cid"]]["n_tasks"], "sif": _sif_ok(e["cid"]), "stats": stats[e["cid"]],
               "tasks": [{"order": t["order"], "title": t["title"], "summary": t.get("summary") or ""} for t in CHAINS[e["cid"]]["tasks"]]}
              for e in cfg.get("showcase_chains") or [] if e.get("cid") in stats]
    cids = [c["cid"] for c in chains]
    return {"run": run, "margin": cfg.get("showcase_margin", 0.2),
            "default_cid": _default_cid() if _default_cid() in cids else (cids[0] if cids else None),
            "price_row": row, "kinds": SHOWCASE_KINDS, "kind_notes": KIND_NOTES, "arms": {k: ALL_ARMS[k] for k in SHOWCASE_KINDS},
            "chains": chains}


class ConfigRunReq(BaseModel):
    cid: str
    configs: list                 # config ids from demo/configs.json, or inline {kind, provider, model}
    step_limit: int = 100
    memory_system: str | None = None   # Knowledge Vault version for kv columns (settings); None = default


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
        if c.get("kind") not in LIVE_SYSTEMS:
            errors.append(f"{c.get('label', c.get('id', c.get('kind')))}: {_live_kind_error(c.get('kind'))}"); continue
        rid = config_run_id(c["provider"], c["model"])
        try:
            lr = LiveReq(cid=req.cid, kind=c["kind"], run_id=rid, provider=c["provider"], model=c["model"], step_limit=req.step_limit,
                         memory_system=_kv_pref(req.memory_system) if c["kind"] == "kv" else None)
            with _LIVE_START_LOCK:
                why = _live_duplicate(lr)
                j = None if why else _start_live(lr)
            if why:
                errors.append(why); continue                      # the others still start
            j["config"] = c.get("id") or c.get("short") or f"{c['kind']}+{c['model']}"
            jobs.append(j)
        except HTTPException as e:
            errors.append(f"{c.get('label', c.get('id', c['kind']))}: {e.detail}")
    return {"jobs": jobs, "errors": errors}


# ---------------------------------------------------------------- custom sandbox projects ("your own task")

CUSTOM_RUN = "custom"
PROJECTS_FILE = RUNS / CUSTOM_RUN / "projects.json"
CUSTOM_KINDS = {"kv": "mem_fusednote", "mem0": "mem_factsall", "none": "seq", "memop": "mem_memop"}   # memop: API only, the Agent page doesn't offer it yet
# rename / delete a sandbox project: its projects.json entry, the per-arm run dirs RUNS/custom/<name>__s0__<arm>/
# (trajectories, results, memory store) and everything under DEMO_CUSTOM_WORK/<name>/ (same default as custom_runner.py):
# the seed <name>/seed/ (the files the user uploaded or the repo they cloned; every memory system's workspace starts as
# a copy of it) and the per-arm workspaces <name>/<arm>/repo/
CUSTOM_WORK = pathlib.Path(os.environ.get("DEMO_CUSTOM_WORK", f"/scratch/{os.environ.get('USER', 'nobody')}/kv/custom"))
PROJECT_SOURCES = ("empty", "upload", "git", "image")    # how a project starts: empty folder, your files, your git repo, example repo
UPLOAD_MAX_FILES = 5000                                   # files uploaded into one project (zip members count one each)
_PROJECTS_LOCK = threading.RLock()                        # projects.json read-modify-write (endpoints run in a thread pool)
_UPLOAD_LOCKS = collections.defaultdict(threading.Lock)   # one upload at a time per project
MB = 1024 * 1024


def _mb_env(name, default):
    """Size limit in bytes from an env var given in MB (read per call: tests and operators can change it)."""
    try:
        return int(float(os.environ.get(name, str(default))) * MB)
    except ValueError:
        return int(default * MB)


def _mb(n):
    return f"{n / MB:g} MB"


def _incoming_dir():
    """Scratch dir for request bodies and download zips (under CUSTOM_WORK = the data disk; '.incoming' can never be a
    project name). Leftovers older than 6 h (a crash mid-request) are removed."""
    d = CUSTOM_WORK / ".incoming"
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for e in os.scandir(d):
        try:
            if now - e.stat(follow_symlinks=False).st_mtime > 6 * 3600:
                shutil.rmtree(e.path) if e.is_dir(follow_symlinks=False) else os.unlink(e.path)
        except OSError:
            pass
    return d


def _tree_size(path):
    """Bytes under path (files and links themselves, never followed)."""
    total = 0
    stack = [str(path)]
    while stack:
        try:
            ents = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for e in ents:
            try:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                else:
                    total += e.stat(follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def _real_dir(p):
    """p is a directory and not a symlink (paths inside workspaces are written by the agent: never follow its links)."""
    try:
        return stat.S_ISDIR(os.lstat(p).st_mode)
    except OSError:
        return False


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
    """Custom projects appear as chains (cid = project name) so /api/session, /api/runchains and the compare page work;
    their "repo" is the project's subtitle (_project_label)."""
    for name, p in _projects().items():
        CHAINS[name] = {"cid": name, "repo": _project_label(p) or p["sandbox"], "source": "custom", "date_range": p.get("created"),
                        "docker_image": f"custom:{p['sandbox']}", "tag": p["sandbox"], "n_tasks": len(p["tasks"]),
                        "tasks": [{"order": t["order"], "instance": t["instance"], "title": t["problem_statement"].strip().splitlines()[0][:160],
                                   "summary": _prompt_summary(t["problem_statement"]),
                                   "problem_statement": t["problem_statement"]} for t in p["tasks"]], "custom": True}


def _sandboxes():
    """Images under SIF_DIR: chain repo images (named by docker tag) and generic sandbox-*.sif images."""
    out = []
    by_tag = {c["tag"]: c for c in list(CHAINS.values()) if not c.get("custom")}
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
                out.append({"id": tag, "kind": "repo", "label": f"{c['repo']} — the repository at the commit its ChainSWE turns start from", "chain": c["cid"]})
            else:                                   # anything that is not a chain image: python:3.12 -> 3.12.sif, sandbox-*.sif, ...
                out.append({"id": tag, "kind": "generic", "label": f"{_image_desc(tag)} — an empty folder with git"})
    return out


def _default_generic(sbs=None):
    """The image of empty / your-files / git projects: 3.12 when present, else the first generic image (None: none)."""
    gens = [s for s in (_sandboxes() if sbs is None else sbs) if s["kind"] == "generic"]
    return next((s for s in gens if s["id"] == "3.12"), gens[0] if gens else None)


def _image_desc(sandbox):
    """'3.12' (this VM) or 'sandbox-python-3.12' (the Docker image's name for python:3.12) -> 'Python 3.12'; other
    generic images by their name."""
    tag = sandbox[len("sandbox-"):] if sandbox.startswith("sandbox-") else sandbox
    m = re.fullmatch(r"(?:python-?)?(\d+(?:\.\d+)*)", tag, re.I)
    return f"Python {m.group(1)}" if m else tag


@app.get("/api/sandboxes")
def sandboxes():
    sbs = _sandboxes()
    d = _default_generic(sbs)
    if d:
        d["default"] = True
    return sbs


@app.get("/api/custom/projects")
def custom_projects(kv: str | None = None):
    ps = _projects()
    out = []
    for name, p in ps.items():
        arms = {}
        for kind in CUSTOM_KINDS:
            arm = _arm_for(CUSTOM_RUN, name, kind, kv)
            x = load_chain_arm(CUSTOM_RUN, arm, name, kind)
            if x:
                arms[kind] = {"arm": arm, "orders": [s["order"] for s in x["sessions"]], "usd": round(sum(s["tok"]["usd"] for s in x["sessions"]), 4)}
        out.append({"name": name, "sandbox": p["sandbox"], "label": _project_label(p), "created": p.get("created"),
                    "tasks": [{"order": t["order"], "instance": t["instance"], "title": t["problem_statement"].strip().splitlines()[0][:120],
                               "summary": _prompt_summary(t["problem_statement"])} for t in p["tasks"]],
                    "arms": arms, "source": _project_source(p),
                    "downloads": [k for k in CUSTOM_KINDS if _workspace_for(name, k, kv)]})   # kinds with a workspace to download
    return out


def _project_source(p):
    """How the project started: the stored source, inferred for projects made before sources existed (chain image ->
    image, generic image -> empty)."""
    s = p.get("source")
    if isinstance(s, dict) and s.get("kind") in PROJECT_SOURCES:
        return s
    tags = {c["tag"]: c["cid"] for c in list(CHAINS.values()) if not c.get("custom")}
    return {"kind": "image", "chain": tags[p.get("sandbox")]} if p.get("sandbox") in tags else {"kind": "empty"}


def _project_label(p):
    """The project's subtitle as the pages show it: 'Empty folder · Python 3.12', 'Your files · …', '<host>/<owner>/<repo>
    · …', 'Example repo · owner/repo'. Labels stored before sources existed ('<repo> — repo at the chain's base commit
    (<cid>)', '3.12 — empty workspace (…)') are rebuilt from the source and the image; projects.json is left as it is."""
    lab, s = p.get("label") or "", _project_source(p)
    if s["kind"] in ("git", "upload") or (lab and " — " not in lab):
        return lab or p.get("sandbox") or ""
    if s["kind"] == "image" or "repo at the chain" in lab:
        repo = CHAINS[s["chain"]]["repo"] if s.get("chain") in CHAINS else lab.split(" — ")[0]
        return f"Example repo · {repo}" if repo else "Example repo"
    return f"Empty folder · {_image_desc(p.get('sandbox') or '')}"


_register_custom_chains()                # at import, once the label helpers above exist


def _workspace_arms(kind, kv=None):
    """Arm directories a kind's workspace can live in (kv: the viewer's version first, as _arm_for)."""
    return [f"mem_{x}" for x in dict.fromkeys([_kv_pref(kv), *KV_SYSTEMS])] if kind == "kv" else [CUSTOM_KINDS[kind]]


def _workspace_for(name, kind, kv=None):
    """CUSTOM_WORK/<name>/<arm>/repo of that memory system when it has a git repository (it ran at least once)."""
    for a in _workspace_arms(kind, kv):
        ws = CUSTOM_WORK / name / a / "repo"
        if _real_dir(CUSTOM_WORK / name / a) and _real_dir(ws) and _real_dir(ws / ".git"):
            return ws
    return None


def _arm_workspaces(name):
    """Every existing arm workspace of the project (CUSTOM_WORK/<name>/<arm>/repo; the seed is not one)."""
    try:
        ents = list(os.scandir(CUSTOM_WORK / name))
    except OSError:
        return []
    return sorted(pathlib.Path(e.path) / "repo" for e in ents
                  if e.name != "seed" and not e.name.startswith(".") and e.is_dir(follow_symlinks=False)
                  and _real_dir(pathlib.Path(e.path) / "repo"))


class ProjectReq(BaseModel):
    """A new sandbox project; every field is optional ({} = an empty folder). Older clients send {name, sandbox}."""
    name: str | None = None        # empty: named after the git repo / the first words of hint / "project", made unique
    source: str | None = None      # empty | upload | git | image; None: image for a chain image, else empty
    sandbox: str | None = None     # image: a chain image id (/api/sandboxes kind repo); others: default generic image
    git_url: str | None = None     # git: public https:// URL (credentials in it are used for the clone only, never stored)
    git_ref: str | None = None     # git: branch or tag (default: the remote's default branch)
    hint: str | None = None        # the task the user typed: names the project when name is empty


def _project_name(raw):
    """Letters, digits, _ . - (anything else -> '-'), at most 48 chars, never starting or ending with '.' or '-':
    the name is a directory under CUSTOM_WORK, so '..' (or any dot name) must never be one."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", (raw or "").strip()).strip(".-")[:48].rstrip(".-")


def _name_taken(name, ps):
    return name in ps or name in CHAINS or os.path.lexists(CUSTOM_WORK / name) or bool(_project_dirs(name))


def _unique_project_name(base, ps):
    """base, base-2, base-3, ... — the first one free in projects.json, CHAINS, CUSTOM_WORK and the run dirs."""
    base = _project_name(base) or "project"
    for i in range(1, 1000):
        suf = "" if i == 1 else f"-{i}"
        cand = (base[:48 - len(suf)].rstrip(".-") or "project") + suf
        if not _name_taken(cand, ps):
            return cand
    raise HTTPException(409, "no free project name left — please pick a name")


def _hint_slug(hint):
    """The first ~5 words of the task text as a lowercase slug of at most 40 characters ('' when it has none)."""
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", hint or "")[:5]]
    out = words[0][:40] if words else ""
    for w in words[1:]:
        if len(out) + 1 + len(w) > 40:
            break
        out += "-" + w
    return out


_GIT_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
# "github.com/owner/repo" as copied from an address bar: a URL without a scheme whose first part is a host name
# (dotted name, IP literal or localhost, optional port) is read as https:// (the host rules below still apply)
_HOSTLIKE_RE = re.compile(r"^(?:localhost|\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+)(?::\d{1,5})?(?:/|$)")
# Credential helper for a clone whose URL carried user:token@ — git asks it only after the server answered 401, it
# answers only for that host, and the secret comes from the environment (never the command line, a file or the log).
_GIT_CRED_HELPER = ('!f() { test "$1" = get || exit 0; h=; while IFS= read -r l; do case "$l" in host=*) h="${l#host=}";; esac; done; '
                    'test "$h" = "$KVGIT_HOST" || exit 0; printf "username=%s\\npassword=%s\\n" "$KVGIT_USER" "$KVGIT_PASS"; }; f')


def _host_addresses(host, port):
    """Addresses a host name resolves to (a function of its own so tests can stub DNS)."""
    return [ai[4][0] for ai in socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)]


def _check_public_host(host, port):
    """400 unless host (an IP literal, or every address its name resolves to) is a public address."""
    try:
        addrs = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addrs = _host_addresses(host, port)
        except OSError:
            raise HTTPException(400, f"cannot resolve {host} — check the URL")
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise HTTPException(400, f"{host} is a private or local address — only public repositories can be cloned")


def _check_git(url, ref):
    """A user's git URL and ref -> what _git_clone needs, or 400. https only ("github.com/owner/repo" without a scheme
    counts as https://, _HOSTLIKE_RE); a public host (no localhost, no loopback / private / link-local address, as a
    literal or by DNS); no whitespace; at most 500 chars; ref = a branch or tag name.
    DEMO_GIT_ALLOW_FILE=1 (tests only) also accepts file:// URLs and local paths."""
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "paste the repository's https:// URL, e.g. https://github.com/owner/repo")
    if len(url) > 500:
        raise HTTPException(400, "the git URL is too long (at most 500 characters)")
    if re.search(r"[\s\x00-\x1f\x7f]", url):
        raise HTTPException(400, "the git URL must not contain spaces")
    ref = (ref or "").strip() or None
    if ref is not None and (not _GIT_REF_RE.match(ref) or ref.startswith("-")):
        raise HTTPException(400, "the branch must be a plain branch or tag name (letters, digits and . _ / -)")
    g = {"ref": ref, "user": None, "password": None, "cred_host": None, "secrets": [], "file": False}
    if os.environ.get("DEMO_GIT_ALLOW_FILE") == "1" and (url.startswith("file://") or url.startswith("/")):
        base = pathlib.PurePosixPath(url[len("file://"):] if url.startswith("file://") else url).name
        repo = base[:-4] if base.endswith(".git") else base
        return {**g, "clone_url": url, "clean_url": url, "repo": repo, "where": f"{repo} (local)", "file": True}
    if "://" not in url and _HOSTLIKE_RE.match(url):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() != "https":
        raise HTTPException(400, "only https:// git URLs are supported, e.g. https://github.com/owner/repo")
    try:
        host, port = parts.hostname, parts.port
    except ValueError:
        raise HTTPException(400, "the git URL has an invalid host or port")
    if not host:
        raise HTTPException(400, "the git URL needs a host, e.g. https://github.com/owner/repo")
    if parts.query or parts.fragment:
        raise HTTPException(400, "use the plain repository URL (no ?query or #fragment)")
    if host == "localhost" or host.endswith(".localhost"):
        raise HTTPException(400, "local addresses are not allowed — only public repositories can be cloned")
    segs = [s for s in parts.path.split("/") if s]
    if not segs:
        raise HTTPException(400, "the git URL needs the repository path, e.g. https://github.com/owner/repo")
    _check_public_host(host, port)
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    repo = segs[-1][:-4] if segs[-1].endswith(".git") else segs[-1]
    clean = urllib.parse.urlunsplit(("https", netloc, parts.path, "", ""))
    g.update(clone_url=clean, clean_url=clean, repo=repo, where="/".join([netloc, *segs[:-1], repo]))
    if parts.username is not None or parts.password is not None:
        user, pw = urllib.parse.unquote(parts.username or ""), urllib.parse.unquote(parts.password or "")
        if re.search(r"[\x00-\x1f\x7f]", user + pw):
            raise HTTPException(400, "the credentials in the git URL contain control characters")
        g.update(user=user, password=pw, cred_host=netloc,
                 secrets=[s for s in {user, pw, parts.username or "", parts.password or ""} if len(s) >= 3])
    return g


def _scrub_secrets(text, g):
    for s in sorted(g.get("secrets") or [], key=len, reverse=True):
        text = text.replace(s, "***")
    return text


def _git_env(extra=None):
    """Environment for git on the host: no system or user config (no credential helper that could store a token, no
    url rewrites, no filters), no prompts, an empty HOME (no ~/.netrc); the server's proxy / CA settings are kept."""
    home = CUSTOM_WORK / ".githome"
    home.mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin", "HOME": str(home), "XDG_CONFIG_HOME": str(home),
           "LANG": "C", "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_ATTR_NOSYSTEM": "1", "GCM_INTERACTIVE": "never"}
    for k in ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "GIT_SSL_CAINFO"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env.update(extra or {})
    return env


def _kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)               # its own session (start_new_session): git + git-remote-https + index-pack
    except (ProcessLookupError, PermissionError):
        try:
            p.kill()
        except ProcessLookupError:
            pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _git_clone(g, seed):
    """Shallow clone of a checked URL (_check_git) into seed, on the host: https only, no prompts, credentials only via
    _GIT_CRED_HELPER, DEMO_GIT_TIMEOUT s (default 180), DEMO_SEED_MAX_MB incl. .git (default 500). -> source fields
    {url, ref?, commit?}; on any failure the partial clone is removed and HTTPException(400) says why: "git clone failed: "
    + a known cause in plain words (missing branch, private or missing repository, unreachable server), else git's last lines."""
    timeout = float(os.environ.get("DEMO_GIT_TIMEOUT", "180"))
    cap = _mb_env("DEMO_SEED_MAX_MB", 500)
    cfg = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "credential.helper="]
    if g["file"]:
        cfg += ["-c", "protocol.file.allow=always"]
    extra = {}
    if g["cred_host"]:
        cfg += ["-c", "credential.helper=" + _GIT_CRED_HELPER]
        extra = {"KVGIT_HOST": g["cred_host"], "KVGIT_USER": g["user"], "KVGIT_PASS": g["password"]}
    cmd = ["git", *cfg, "clone", "--depth", "1", "--single-branch", "--no-tags", *(["--branch", g["ref"]] if g["ref"] else []),
           "--", g["clone_url"], str(seed)]
    seed.parent.mkdir(parents=True, exist_ok=True)
    why, rc = None, None
    with tempfile.TemporaryFile(dir=_incoming_dir()) as errf:
        p = subprocess.Popen(cmd, env=_git_env(extra), stdin=subprocess.DEVNULL, stdout=errf, stderr=errf, start_new_session=True)
        t0 = time.time()
        while True:
            try:
                rc = p.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                pass
            if time.time() - t0 > timeout:
                why = f"git clone took longer than {timeout:g} s — try a smaller repository"
            elif _tree_size(seed) > cap:
                why = f"the repository is larger than {_mb(cap)} — try a smaller one"
            if why:
                _kill_group(p)
                break
        errf.seek(0)
        err = errf.read().decode("utf-8", "replace")
    if why is None and rc != 0:
        lines = [l.strip() for l in err.splitlines() if l.strip() and not l.startswith("Cloning into")]
        tail = " ".join(lines[-3:])[-400:] or f"exit {rc}"
        branch = re.search(r"Remote branch (\S+) not found", tail)
        hint = (f"no branch or tag named {g['ref'] or branch.group(1)} in this repository" if branch else
                "the repository does not exist or is private — only public repositories can be cloned without a token"
                if re.search(r"could not read (Username|Password)|Authentication failed|terminal prompts disabled|not found", tail) else
                "the server could not be reached" if re.search(r"Could not resolve host|Failed to connect|timed out", tail) else "")
        why = f"git clone failed: {hint or tail}"          # a known cause in plain words; git's own last lines otherwise
    if why is None and _tree_size(seed) > cap:
        why = f"the repository is larger than {_mb(cap)} — try a smaller one"
    if why:
        shutil.rmtree(seed, ignore_errors=True)
        raise HTTPException(400, _scrub_secrets(why, g))
    genv = _git_env()
    subprocess.run(["git", "-C", str(seed), "remote", "set-url", "origin", g["clean_url"]], env=genv, capture_output=True, timeout=30)
    r = subprocess.run(["git", "-C", str(seed), "rev-parse", "--verify", "-q", "HEAD"], env=genv, capture_output=True, text=True, timeout=30)
    sha = r.stdout.strip() if r.returncode == 0 else ""
    return {"url": g["clean_url"], **({"ref": g["ref"]} if g["ref"] else {}), **({"commit": sha} if sha else {})}


@app.post("/api/custom/project")
def custom_project(req: ProjectReq):
    """Create a sandbox project: an empty folder, your files (uploaded next via /files), a clone of your git repo, or
    an example repo (a ChainSWE chain image at its base commit). 409 when an explicit name exists already."""
    sbs = _sandboxes()
    sb = None
    if req.sandbox:
        sb = next((s for s in sbs if s["id"] == req.sandbox), None)
        if not sb:
            raise HTTPException(400, f"unknown or missing sandbox image {req.sandbox}")
    src = (req.source or "").strip().lower() or (("image" if sb["kind"] == "repo" else "empty") if sb else ("git" if req.git_url else "empty"))
    if src not in PROJECT_SOURCES:
        raise HTTPException(400, f"source must be one of {', '.join(PROJECT_SOURCES)}")
    if src == "image":
        if not sb or sb["kind"] != "repo":
            raise HTTPException(400, "an example-repo project needs sandbox = a task image id (/api/sandboxes entries of kind repo)")
    elif sb and sb["kind"] != "generic":
        raise HTTPException(400, f"{req.sandbox} is a ChainSWE task image: create it with source image")
    else:
        sb = sb or _default_generic(sbs)
        if not sb:
            raise HTTPException(400, "this server has no generic sandbox image (e.g. 3.12.sif under RSM_SIF_DIR)")
    g = _check_git(req.git_url, req.git_ref) if src == "git" else None
    raw = (req.name or "").strip()
    with _PROJECTS_LOCK:
        ps = _projects()
        if raw:
            name = _project_name(raw)
            if not name:
                raise HTTPException(400, "the project name needs letters or digits")
            if name in CHAINS and not CHAINS[name].get("custom"):
                raise HTTPException(400, "name collides with a ChainSWE task")
            if _name_taken(name, ps):
                raise HTTPException(409, f"a project named {name} already exists")
        else:
            name = _unique_project_name(g["repo"] if g else _hint_slug(req.hint), ps)
        work = CUSTOM_WORK / name
        work.mkdir(parents=True)                     # reserves the name while a clone runs (_name_taken sees it)
    try:
        source = {"kind": src}
        if g:
            source.update(_git_clone(g, work / "seed"))
        if src == "image":
            source["chain"] = sb.get("chain")
        else:
            source["files"] = 0
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    desc = _image_desc(sb["id"])
    if src == "image":
        label = f"Example repo · {CHAINS[sb['chain']]['repo']}"
    elif src == "git":
        label = f"{g['where']}{'@' + g['ref'] if g['ref'] else ''} · {desc}"
    else:
        label = f"{'Empty folder' if src == 'empty' else 'Your files'} · {desc}"
    with _PROJECTS_LOCK:
        ps = _projects()
        ps[name] = {"sandbox": sb["id"], "label": label, "created": time.strftime("%Y-%m-%d %H:%M"), "tasks": [], "source": source}
        _save_projects(ps)
        _register_custom_chains()
    return {"name": name, **ps[name]}


def _project_dirs(name):
    return [d for d in (RUNS / CUSTOM_RUN).glob(f"{glob.escape(name)}__s0__*") if d.is_dir()]


def _project_busy(name):
    return any(j.get("custom") and j.get("cid") == name and j.get("status") == "running" for j in JOBS.values())


def _existing_project(name):
    ps = _projects()
    if name not in ps:
        raise HTTPException(404, "unknown project")
    if _project_busy(name):
        raise HTTPException(409, "a turn is running in this project — stop it first")
    return ps


class ProjectRenameReq(BaseModel):
    name: str
    new_name: str


@app.post("/api/custom/project/rename")
def custom_project_rename(req: ProjectRenameReq):
    with _UPLOAD_LOCKS[req.name], _PROJECTS_LOCK:          # lock order: upload lock, then projects lock
        ps = _existing_project(req.name)
        new = _project_name(req.new_name)
        if not new:
            raise HTTPException(400, "new name required")
        if new == req.name:
            return {"name": new, **ps[new]}
        if new in ps or new in CHAINS or _project_dirs(new) or os.path.lexists(CUSTOM_WORK / new):
            raise HTTPException(400, f"name {new} is already taken")
        for d in _project_dirs(req.name):
            d.rename(d.with_name(new + d.name[len(req.name):]))
        if (CUSTOM_WORK / req.name).exists():
            (CUSTOM_WORK / req.name).rename(CUSTOM_WORK / new)
        ps = {(new if k == req.name else k): v for k, v in ps.items()}   # keep the sidebar order
        _save_projects(ps)
        CHAINS.pop(req.name, None)
        _register_custom_chains()
        for j in JOBS.values():                                          # finished jobs keep pointing at the project
            if j.get("custom") and j.get("cid") == req.name:
                j["cid"] = new
        return {"name": new, **ps[new]}


@app.delete("/api/custom/project/{name}")
def custom_project_delete(name: str):
    with _UPLOAD_LOCKS[name], _PROJECTS_LOCK:
        ps = _existing_project(name)
        for d in _project_dirs(name):
            shutil.rmtree(d)
        if (CUSTOM_WORK / name).exists():
            shutil.rmtree(CUSTOM_WORK / name)
        ps.pop(name)
        _save_projects(ps)
        CHAINS.pop(name, None)
        return {"deleted": name}


# ---------------------------------------------------------------- your files in a sandbox project (upload / download)

def _upload_parts(path):
    """A user-supplied relative path -> its components. Relative, no '..', no NUL / backslash / control characters,
    names <= 255 bytes, whole path <= 400 chars; '.' and empty components are dropped ('./a//b' = 'a/b')."""
    p = path or ""
    show = p[:80]
    if not p.strip() or "\\" in p or re.search(r"[\x00-\x1f\x7f]", p):
        raise HTTPException(400, f"bad path {show!r}: use a relative path with / between folders")
    if p.startswith("/"):
        raise HTTPException(400, f"bad path {show!r}: it must be relative to the project folder")
    parts = [c for c in p.split("/") if c not in ("", ".")]
    if not parts or ".." in parts:
        raise HTTPException(400, f"bad path {show!r}: '..' is not allowed")
    if any(len(c.encode()) > 255 for c in parts) or len("/".join(parts)) > 400:
        raise HTTPException(400, "path too long (at most 400 characters, 255 per name)")
    return parts


def _has_git_dir(parts):
    return any(c.lower() == ".git" for c in parts)


def _check_target(base, parts):
    """400 when writing base/parts would pass through a link or a file, or replace a folder (checked before writing
    anything, for every target, so an upload is refused as a whole)."""
    cur = base
    for i, comp in enumerate(parts):
        cur = cur / comp
        try:
            m = os.lstat(cur).st_mode
        except FileNotFoundError:
            return
        if i < len(parts) - 1 and not stat.S_ISDIR(m):
            raise HTTPException(400, f"{'/'.join(parts)}: {'/'.join(parts[:i + 1])} is {'a link' if stat.S_ISLNK(m) else 'a file'} "
                                     "in the project, not a folder")
        if i == len(parts) - 1 and stat.S_ISDIR(m):
            raise HTTPException(400, f"{'/'.join(parts)} is a folder in the project")


def _open_target(base, parts, mode=0o644):
    """Open base/parts for writing: missing folders are created; a link (or special file) at the target is replaced,
    never written through — workspaces contain whatever the agent made there. -> binary file object."""
    cur = base
    for comp in parts[:-1]:
        cur = cur / comp
        try:
            os.mkdir(cur)
        except FileExistsError:
            if not _real_dir(cur):
                raise HTTPException(400, f"{'/'.join(parts)}: {comp} is not a folder in the project")
    target = cur / parts[-1]
    try:
        m = os.lstat(target).st_mode
        if stat.S_ISDIR(m):
            raise HTTPException(400, f"{'/'.join(parts)} is a folder in the project")
        if not stat.S_ISREG(m):
            os.unlink(target)
    except FileNotFoundError:
        pass
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    os.fchmod(fd, mode)
    return os.fdopen(fd, "wb")


def _write_everywhere(bases, parts, src, mode=0o644):
    """Copy the readable binary stream src to base/parts for every base; partial files are removed on failure."""
    outs = []
    try:
        for b in bases:
            outs.append((b, _open_target(b, parts, mode)))
        n = 0
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            n += len(chunk)
            for _, f in outs:
                f.write(chunk)
        for _, f in outs:
            f.close()
        return n
    except BaseException:
        for b, f in outs:
            f.close()
            try:
                os.unlink(b.joinpath(*parts))
            except OSError:
                pass
        raise


def _unzip_upload(zpath, parts, bases, allow_git, room, slots, per_file):
    """Extract the uploaded zip into its own folder in every base. Every member path follows the upload path rules
    (one bad name refuses the whole zip: zip-slip); links, devices and macOS __MACOSX/ entries are skipped, and .git/
    members once workspaces exist; one top-level folder shared by every member is stripped (repo-main/...). Sizes are
    the members' declared uncompressed sizes (zipfile never returns more than that). -> {bytes, files, skipped}."""
    import zlib
    shown = "/".join(parts)
    try:
        zf = zipfile.ZipFile(zpath)
    except (zipfile.BadZipFile, OSError):
        raise HTTPException(400, f"{shown} is not a valid .zip file")
    with zf:
        cands, skipped = [], 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind and kind != stat.S_IFREG:          # symlink, device, fifo, socket
                skipped += 1
                continue
            try:
                mparts = _upload_parts(info.filename)
            except HTTPException:
                raise HTTPException(400, f"{shown}: unsafe path inside the zip ({info.filename[:80]!r}) — nothing was extracted")
            if "__MACOSX" in mparts:
                continue
            if info.flag_bits & 0x1:
                raise HTTPException(400, f"{shown}: password-protected zip files are not supported")
            cands.append((info, mparts))
        strip = len({m[0] for _, m in cands}) == 1 and all(len(m) >= 2 for _, m in cands)
        plan = []
        for info, m in cands:
            full = parts[:-1] + (m[1:] if strip else m)
            if len("/".join(full)) > 400:
                raise HTTPException(400, f"{shown}: {info.filename[:80]!r} gives a path longer than 400 characters")
            if _has_git_dir(full) and not allow_git:
                skipped += 1
                continue
            plan.append((info, full))
        # members that clash with each other (a file "a" and a file "a/b", or the same path twice) are refused before anything is
        # written; _check_target below only compares them with what is already on disk
        paths = {tuple(full) for _, full in plan}
        if len(paths) < len(plan):
            raise HTTPException(400, f"{shown}: the zip holds the same path twice — nothing was extracted")
        clash = next((f for f in paths if any(f[:i] in paths for i in range(1, len(f)))), None)
        if clash:
            raise HTTPException(400, f"{shown}: {'/'.join(clash)} needs a folder where the zip also has a file — nothing was extracted")
        if len(plan) > slots:
            raise HTTPException(413, f"{shown} has {len(plan)} files: a project takes at most {UPLOAD_MAX_FILES} uploaded files")
        big = next((i for i, _ in plan if i.file_size > per_file), None)
        if big:
            raise HTTPException(413, f"{shown}: {big.filename} is larger than {_mb(per_file)} (the limit per file)")
        total = sum(i.file_size for i, _ in plan)
        if total > room:
            raise HTTPException(413, f"{shown} unpacks to {_mb(total)}: more than the room left in the project")
        for _, full in plan:
            for b in bases:
                _check_target(b, full)
        n = 0
        for info, full in plan:
            mode = 0o755 if (info.external_attr >> 16) & 0o111 else 0o644
            try:
                with zf.open(info) as src:
                    n += _write_everywhere(bases, full, src, mode)
            except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError) as e:
                raise HTTPException(400, f"{shown}: cannot extract {info.filename[:80]!r} ({type(e).__name__}: {str(e)[:80]})")
        return {"bytes": n, "files": len(plan), "skipped": skipped, "unzipped": True}


def _place_upload(name, parts, tmp, size, unzip):
    """Second half of an upload (thread pool): the body is in tmp. Before the first task the seed is the only target;
    once memory systems have workspaces, the file goes into the seed and into each of them (no .git paths then)."""
    with _UPLOAD_LOCKS[name]:
        p = _projects().get(name)
        if p is None:
            raise HTTPException(404, "unknown project")
        if _project_busy(name):
            raise HTTPException(409, "a turn is running in this project — wait for it or stop it")
        workspaces = _arm_workspaces(name)
        if workspaces and _has_git_dir(parts):
            raise HTTPException(400, ".git files can only be uploaded before the project's first turn")
        seed = CUSTOM_WORK / name / "seed"
        seed.mkdir(parents=True, exist_ok=True)
        if not _real_dir(seed):
            raise HTTPException(500, "the project's seed folder is not a directory")
        bases = [seed, *workspaces]
        done = int(_project_source(p).get("files") or 0)
        seed_max, per_file = _mb_env("DEMO_SEED_MAX_MB", 500), _mb_env("DEMO_UPLOAD_MAX_MB", 50)
        used = _tree_size(seed)
        shown = "/".join(parts)
        if unzip:
            res = _unzip_upload(tmp, parts, bases, not workspaces, seed_max - used, UPLOAD_MAX_FILES - done, per_file)
        else:
            if done + 1 > UPLOAD_MAX_FILES:
                raise HTTPException(413, f"a project takes at most {UPLOAD_MAX_FILES} uploaded files")
            try:
                old = os.lstat(seed.joinpath(*parts))
                old = old.st_size if stat.S_ISREG(old.st_mode) else 0
            except OSError:
                old = 0
            if used - old + size > seed_max:
                raise HTTPException(413, f"{shown}: the project's files would exceed {_mb(seed_max)}")
            for b in bases:
                _check_target(b, parts)
            with open(tmp, "rb") as src:
                _write_everywhere(bases, parts, src)
            res = {"bytes": size, "files": 1}
        with _PROJECTS_LOCK:
            ps = _projects()
            if name in ps:
                s = dict(_project_source(ps[name]))
                s["files"] = int(s.get("files") or 0) + res["files"]
                ps[name]["source"] = s
                _save_projects(ps)
        return {"path": shown, **res, "workspaces": len(workspaces)}


@app.post("/api/custom/project/{name}/files")
async def custom_project_files(name: str, request: Request, path: str = "", unzip: bool = False):
    """Upload one file into a sandbox project; the request body is the raw file (application/octet-stream), streamed to
    disk. Limits: DEMO_UPLOAD_MAX_MB per file (default 50), DEMO_SEED_MAX_MB for the project's files (default 500),
    UPLOAD_MAX_FILES files -> 413. unzip=1 with a .zip path extracts it into the zip's folder instead of keeping it."""
    p = _projects().get(name)
    if p is None:
        raise HTTPException(404, "unknown project")
    if _project_source(p)["kind"] not in ("empty", "upload", "git"):
        raise HTTPException(400, "uploads work for empty, upload and git projects")
    if _project_busy(name):
        raise HTTPException(409, "a turn is running in this project — wait for it or stop it")
    parts = _upload_parts(path)
    unzip = bool(unzip) and parts[-1].lower().endswith(".zip")
    per_file = _mb_env("DEMO_UPLOAD_MAX_MB", 50)
    too_big = HTTPException(413, f"{'/'.join(parts)} is larger than {_mb(per_file)} (the limit per file)")
    cl = request.headers.get("content-length", "")
    if cl.isdigit() and int(cl) > per_file:
        raise too_big
    tmp = _incoming_dir() / f"{uuid.uuid4().hex}.part"
    try:
        n = 0
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                n += len(chunk)
                if n > per_file:
                    raise too_big
                f.write(chunk)
        return await run_in_threadpool(_place_upload, name, parts, tmp, n, unzip)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _read_head(gd):
    """Commit sha of HEAD of the git dir gd, read without running git in it (its config is written by the agent). The
    agent writes these files too, so each is read with _read_beneath: no symlink followed, a regular file only, at most
    4 KB (packed-refs 8 MB) — a HEAD or ref that is a FIFO, a device or a link to /dev/zero is simply not a commit."""
    hexsha = re.compile(r"[0-9a-f]{40}")

    def small(rel, cap=4096):
        try:
            return _read_beneath(gd, rel, cap).decode(errors="replace")
        except (_NotServed, OSError):
            return ""
    head = small("HEAD").strip()
    if hexsha.fullmatch(head):
        return head
    m = re.fullmatch(r"ref: (refs/[A-Za-z0-9._/-]+)", head)
    if not m or ".." in m.group(1):
        return None
    ref = m.group(1)
    v = small(ref).strip()
    if hexsha.fullmatch(v):
        return v
    for line in small("packed-refs", 8 << 20).splitlines():
        sha, _, r = line.partition(" ")
        if r.strip() == ref and hexsha.fullmatch(sha):
            return sha
    return None


def _unlink_quiet(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def _git_archive_zip(ws):
    """zip of HEAD of the workspace repo ws (a temp file under CUSTOM_WORK; the caller removes it), or None when it has
    no commit. The workspace's own git config is never used on the host (its filters / fsmonitor / hooks could run
    commands): git archive runs in a fresh bare repo that borrows ws/.git/objects as an alternate object store."""
    gd = ws / ".git"
    objs = gd / "objects"
    if not _real_dir(gd) or not _real_dir(objs):
        return None
    if os.path.lexists(objs / "info" / "alternates"):
        raise HTTPException(409, "this workspace borrows objects from another repository — download refused")
    sha = _read_head(gd)
    if not sha:
        return None
    inc = _incoming_dir()
    tmpd = pathlib.Path(tempfile.mkdtemp(dir=inc, prefix="archive-"))
    out = inc / f"{uuid.uuid4().hex}.zip"
    try:
        env = _git_env()
        bare = tmpd / "x.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], env=env, check=True, capture_output=True, timeout=60)
        (bare / "objects" / "info").mkdir(parents=True, exist_ok=True)
        (bare / "objects" / "info" / "alternates").write_text(os.path.abspath(objs) + "\n")
        r = subprocess.run(["git", "--git-dir", str(bare), "archive", "--format=zip", "-o", str(out), sha],
                           env=env, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            _unlink_quiet(out)
            raise HTTPException(500, f"git archive failed: {r.stderr.strip()[-300:]}")
        return out
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


@app.get("/api/custom/project/{name}/download")
def custom_project_download(name: str, kind: str = "kv", kv: str | None = None):
    """The files of one memory system's workspace as they are after its last task (git HEAD), as a zip download."""
    if name not in _projects():
        raise HTTPException(404, "unknown project")
    if kind not in CUSTOM_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(CUSTOM_KINDS)}")
    ws = _workspace_for(name, kind, kv)
    zp = _git_archive_zip(ws) if ws else None
    if zp is None:
        raise HTTPException(404, f"{SHOWCASE_KINDS[kind]} has not run in this project yet")
    return FileResponse(zp, media_type="application/zip", filename=f"{name}-{kind}.zip", background=BackgroundTask(_unlink_quiet, zp))


class CustomTaskReq(BaseModel):
    project: str
    task: str
    kinds: list = ["kv"]              # which arms to run this task on, e.g. ["kv", "mem0"]
    provider: str = "local"
    model: str | None = None
    step_limit: int = 60
    order: int | None = None          # rerun an existing task on another arm; default = append a new task
    fresh: bool = False               # rerun: rewind the arm to just before the task first (custom_runner.py --fresh)
    memory_system: str | None = None  # Knowledge Vault version for kind kv (settings); None = default


@app.post("/api/custom/start")
def custom_start(req: CustomTaskReq):
    if os.environ.get("DEMO_DISABLE_LIVE") == "1":
        raise HTTPException(403, "sandbox runs are disabled on this deployment (DEMO_DISABLE_LIVE=1)")
    with _UPLOAD_LOCKS[req.project]:          # an upload being placed finishes before a runner can copy or commit the files
        return _custom_start(req)


def _custom_start(req):
    with _PROJECTS_LOCK:
        ps = _projects()
        p = ps.get(req.project)
        if not p:
            raise HTTPException(404, "unknown project")
        if req.memory_system and req.memory_system not in KV_SYSTEMS:
            raise HTTPException(400, f"unknown Knowledge Vault version {req.memory_system}")
        kv_sys = _kv_pref(req.memory_system)
        for k in req.kinds:
            if k not in CUSTOM_KINDS:
                raise HTTPException(400, f"kind must be one of {', '.join(CUSTOM_KINDS)}")
            if any(j.get("custom") and j.get("cid") == req.project and j.get("kind") == k and j.get("status") == "running" for j in JOBS.values()):
                raise HTTPException(409, f"The {SHOWCASE_KINDS[k]} run of this project is still going — wait for it or stop it")   # one workspace per project × arm
        if req.order is None:
            text = req.task.strip()
            if not text:
                raise HTTPException(400, "turn text required")
            order = len(p["tasks"]) + 1
            inst = f"{req.project}-{order}-{re.sub(r'[^A-Za-z0-9]+', '-', text.splitlines()[0]).strip('-').lower()[:32] or 'task'}"
            p["tasks"].append({"order": order, "instance": inst, "problem_statement": text, "created": time.strftime("%Y-%m-%d %H:%M")})
            _save_projects(ps)
            _register_custom_chains()
        else:
            order = req.order
            if not any(t["order"] == order for t in p["tasks"]):
                raise HTTPException(400, f"project has no turn {order}")
    model = req.model or (os.environ.get("DEMO_SERVED_NAME", "coder-next") if req.provider == "local" else PV.PROVIDERS.get(req.provider, {}).get("default_model"))
    provider, model = PV.gpt_route(req.provider, model)
    penv, api_base = _provider_env(provider, model)
    jobs = []
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    for kind in req.kinds:
        arm = f"mem_{kv_sys}" if kind == "kv" else CUSTOM_KINDS[kind]
        job = uuid.uuid4().hex[:8]
        log = LIVE_DIR / f"{job}.log"
        env = dict(os.environ)
        env.pop("RSM_ACTOR_NOTHINK", None)
        env.update(penv)
        env.update({"RSM_LEDGER": "1", "RSM_SIF_DIR": str(SIF_DIR), "RSM_RUNS_DIR": str(RUNS),
                    "RSM_HF_HOME": env.get("RSM_HF_HOME", env.get("HF_HOME", str(LIVE_DIR / "hf_home"))),
                    "RSM_LIVE_SAVE": "1", "PYTHONUNBUFFERED": "1"})
        generic = p["sandbox"] not in {c["tag"] for c in list(CHAINS.values()) if not c.get("custom")}
        cmd = [sys.executable, str(HERE / "custom_runner.py"), "--project", req.project, "--order", str(order), "--kind", kind,
               "--sandbox", p["sandbox"], "--step-limit", str(req.step_limit), "--served-name", model, *(["--generic"] if generic else []), *(["--fresh"] if req.fresh else []), *(["--memory-system", kv_sys] if kind == "kv" else [])]
        lf = open(log, "w")
        lf.write("$ " + " ".join(cmd) + "\n\n"); lf.flush()
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
        JOBS[job] = {"id": job, "cid": req.project, "kind": kind, "arm": arm, "run_id": CUSTOM_RUN, "order": order, "custom": True,
                     "started": time.time(), "status": "running", "returncode": None, "log": str(log), "api_base": api_base,
                     "provider": provider, "model": model, "pid": proc.pid}

        def _wait(job=job, proc=proc, lf=lf, arm=arm, env=env):
            rc = proc.wait()
            lf.close()
            scrub.scrub_dir(RUNS / CUSTOM_RUN / f"{req.project}__s0__{arm}", [env.get("RSM_API_KEY", ""), env.get("GOODVISION_API_KEY", "")])
            JOBS[job].update(status=_end_status(job, rc), returncode=rc, finished=time.time())
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
    # probe the endpoints once in the background so the first visitor does not wait for the router; load the showcase
    # chains' recorded sessions (default price row) next to it so the first /api/showcase is instant
    threading.Thread(target=lambda: providers_status(), daemon=True, name="provcheck-warm").start()

    def _warm_showcase():
        try:
            t0 = time.time(); n = len(showcase()["chains"])
            print(f"showcase warm: {n} chains in {time.time() - t0:.1f}s", flush=True)
        except Exception as e:                       # never fatal: the endpoint computes on first use instead
            print(f"showcase warm-up failed: {type(e).__name__}: {e}", flush=True)
    threading.Thread(target=_warm_showcase, daemon=True, name="showcase-warm").start()
    uvicorn.run(app, host=os.environ.get("DEMO_HOST", "127.0.0.1"), port=int(os.environ.get("DEMO_PORT", "8765")))
