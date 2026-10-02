#!/usr/bin/env python3
"""Negative-advantage token mass in the actor27 GRPO updates (2026-09-20).

READ-ONLY.  Nothing here writes into a run directory, a checkpoint, a lane or the
rollout store; the only outputs are the report/json/csv under `train-corpus/metrics/`
and a scratch cache the caller points at with `--cache`.

The question
------------
The trainer's policy loss is a token-SUM over the kept trajectories divided by the
total kept assistant-token count::

    loss = sum_i ( (-A_i) * lp_i + beta * kl_i ).sum() / ntok        (trainer :1943)

so trajectory *i* enters the gradient with weight ``A_i * n_i`` where ``n_i`` is its
number of assistant tokens -- not with weight ``A_i``.  Advantages are group-centred
(they sum to ~0 within a group), but the *token counts* are not, so the signed mass
the gradient actually sees is::

    S = sum_i A_i * n_i  =  G * Cov_group(A, n)   (per group, since sum_i A_i = 0)

Failures run to the step limit and successes stop early, so Cov(A, n) < 0 in a mixed
group and every context-independent token class that occurs at a roughly constant
rate per agent step picks up the net coefficient ``r * S < 0``: reading code, running
tests and the action scaffold are all pushed down.  This script measures S, its
normalised form ``M = S / sum_i |A_i| n_i`` in [-1, 1], the per-class decomposition,
and the two counterfactual weightings that would zero it out.

Sections
--------
A  mass balance per update (shares, signed mass, lengths of A<0 vs A>0)
B  advantage-weighted token mass by action class (view/test/edit/submit/other)
C  behaviour on the test frame, ordered by cumulative dose
D  format-collapse anatomy for dvw6b
E  counterfactual update weightings, without training anything

Stages (each caches to --cache so the expensive one runs once)::

    python neg_mass_hypothesis.py --stage rollouts     # renders trajectories (slow)
    python neg_mass_hypothesis.py --stage exam         # parses test-frame sessions
    python neg_mass_hypothesis.py --stage report       # A/B/D/E + C, writes md/json/csv
"""
import argparse
import collections
import csv
import json
import math
import os
import pathlib
import random
import re
import statistics
import sys

# Path handling (analysis/ledger_table copy): the ledger cost table only uses boot_ci() from this module.
# R is the chainswe repo root (RSM_ROOT, else two levels above this file) and RUNS its runs/ directory
# (RSM_CS_RUNS or RSM_RUNS override). The other stages also need the GRPO trainer modules and rollout results
# of the parent project's train-corpus/, which are not part of this repo.
TC = pathlib.Path(__file__).resolve().parent
R = pathlib.Path(os.environ.get("RSM_ROOT") or TC.parents[1])
sys.path.insert(0, str(TC))

RES = pathlib.Path(os.environ.get("RSM_A9_RES_DIR", str(TC / "actor27_rollout_results")))
RUNS = pathlib.Path(os.environ.get("RSM_CS_RUNS") or os.environ.get("RSM_RUNS") or str(R / "runs"))
METRICS = TC / "metrics"
MODEL = os.environ.get(
    "A27_MODEL",
    "/gpfs/radev/scratch/dijk/pl662/hf_home/hub/models--Qwen--Qwen3.8-27B/"
    "snapshots/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0")

NAME = re.compile(r"^(?P<arm>[^_]+)_s(?P<step>\d+)_(?P<bkey>.+)__g(?P<g>\d+)$")
MAXLEN = 32768
TOK_BUDGET = 350000

# ---------------------------------------------------------------- arm recipes
# Read off the lane scripts in w1/gpuq/{done,running}/cs4b_train_actor27_<arm>.sh.<job>
# (the `$PY .../actor9_grpo_trainer[_prov].py` invocation) and cross-checked against the
# `=== 27B 训练开始 ...` banner and the `[a9] advantage 分母:` line in each lane log.
#   gen   1/2 = old reward (R = 250*res - steps, group-sd scale)
#         3   = success-only reward, group-sd scale
#         4   = success-only, fixed scale 100
#         5   = + partial credit 50, counterfactual/bare centring
#         6   = gen-5 recipe at lr 6e-4, single inner epoch
ARMS = {
    # arm:      G   mix_bare shuf  adv_scale   center   pcred  prov            lr     gen
    "dvw":     dict(G=8,  mb=4, sh=1, scale="group_sd", ctr="group", pc=0.0,  prov=None,
                    lr=2e-4, gen=1, succ=False),
    "mem":     dict(G=8,  mb=0, sh=0, scale="group_sd", ctr="group", pc=0.0,  prov=None,
                    lr=2e-4, gen=1, succ=False),
    "nomem":   dict(G=8,  mb=0, sh=0, scale="group_sd", ctr="group", pc=0.0,  prov=None,
                    lr=2e-4, gen=1, succ=False),
    "dvw2":    dict(G=8,  mb=4, sh=1, scale="group_sd", ctr="group", pc=0.0,  prov=None,
                    lr=2e-4, gen=2, succ=True),
    "dvw3s":   dict(G=8,  mb=4, sh=1, scale="group_sd", ctr="group", pc=0.0,  prov=None,
                    lr=2e-4, gen=3, succ=True),
    "prov3s":  dict(G=8,  mb=4, sh=1, scale="group_sd", ctr="group", pc=0.0,  prov="subgroup",
                    lr=2e-4, gen=3, succ=True),
    "nomem3s": dict(G=8,  mb=8, sh=0, scale="group_sd", ctr="group", pc=0.0,  prov=None,
                    lr=2e-4, gen=3, succ=True),
    "dvw4s":   dict(G=8,  mb=4, sh=1, scale="fixed",    ctr="group", pc=0.0,  prov=None,
                    lr=2e-4, gen=4, succ=True),
    "prov4s":  dict(G=8,  mb=4, sh=1, scale="fixed",    ctr="group", pc=0.0,  prov="subgroup",
                    lr=2e-4, gen=4, succ=True),
    "nomem4s": dict(G=8,  mb=8, sh=0, scale="fixed",    ctr="group", pc=0.0,  prov=None,
                    lr=2e-4, gen=4, succ=True),
    "dvw5s":   dict(G=8,  mb=4, sh=1, scale="fixed",    ctr="bare",  pc=50.0, prov=None,
                    lr=2e-4, gen=5, succ=True),
    "prov5s":  dict(G=8,  mb=4, sh=1, scale="fixed",    ctr="group", pc=0.0,  prov="subgroup",
                    lr=2e-4, gen=5, succ=True),
    "dvw6b":   dict(G=8,  mb=4, sh=1, scale="fixed",    ctr="bare",  pc=50.0, prov=None,
                    lr=6e-4, gen=6, succ=True),
    "prov6b":  dict(G=15, mb=6, sh=3, scale="fixed",    ctr="group", pc=50.0,
                    prov="counterfactual", lr=6e-4, gen=6, succ=True),
    "dvw6g":   dict(G=15, mb=6, sh=3, scale="fixed",    ctr="bare",  pc=50.0, prov=None,
                    lr=6e-4, gen=6, succ=True),
    "nomem6b": dict(G=8,  mb=8, sh=0, scale="fixed",    ctr="bare",  pc=50.0, prov=None,
                    lr=6e-4, gen=6, succ=True),
}
MAIN_ARMS = ["dvw3s", "prov3s", "nomem3s", "dvw4s", "prov4s", "nomem4s",
             "dvw5s", "prov5s", "dvw6b", "prov6b", "dvw6g", "nomem6b"]
OLD_ARMS = ["dvw", "mem", "nomem", "dvw2"]

W_RESOLVE = 250.0
STEP_LIMIT = 100

# ---------------------------------------------------------------- action classes
FENCE = re.compile(r"```(?:mswea_bash_command|bash|sh)?\s*\n(.*?)(?:```|\Z)", re.S)
SUBMIT_RE = re.compile(r"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
TEST_RE = re.compile(
    r"(?:^|[\s;&|(])(?:pytest|py\.test|nosetests|nose2|tox)\b"
    r"|python[0-9.]*\s+-m\s+(?:pytest|unittest|tox|nose2|test)\b"
    r"|python[0-9.]*\s+-m\s+\S*test\S*"
    r"|\bunittest\s+discover\b|\./runtests|\bmake\s+test\b")
# `patch.txt` is the harness's own artefact: the mandated pre-submit turn is
# `git diff -- <files> > patch.txt && cat patch.txt`, and the submit turn is
# `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt`.  Neither edits the repo,
# so both are excluded from the edit class -- otherwise the LAST edit of every trajectory
# is the harness ritual and "did it run a test after its last edit" is false by
# construction (it was, before this exclusion, exactly 100 % in every arm).
EDIT_RE = re.compile(
    r"(?:^|[\s;&|(])sed\s+(?:-[a-zA-Z]*i|--in-place)"
    r"|(?:^|[\s;&|(])patch\s+(?:-|<)"
    r"|(?:^|[\s;&|(])apply_patch\b"
    r"|(?:^|[\s;&|(])(?:cp|mv)\s"
    r"|>>?\s*(?:\S*/)?(?!patch\.txt\b)[\w.\-]+"
    r"\.(?:py|pyi|txt|cfg|ini|toml|json|yaml|yml|rst|md|c|h|cpp|js|ts|go|rs)\b"
    r"|(?:^|[\s;&|(])tee\s"
    r"|<<\s*'?\"?(?:EOF|PYEOF|PATCH|PY)\b"
    r"|open\([^)]*['\"][wa]\+?['\"]"
    r"|\.write_text\(|\.writelines\(|\bshutil\.(?:copy|move)")
VIEW_RE = re.compile(
    r"(?:^|[\s;&|(])(?:cat|less|more|head|tail|grep|egrep|fgrep|rg|ack|find|ls|tree|wc|"
    r"file|stat|pwd|which|nl|diff|cmp|du|env|sort|uniq|awk|cut|readlink|realpath)\b"
    r"|(?:^|[\s;&|(])sed\s+-n"
    r"|(?:^|[\s;&|(])git\s+(?:log|diff|show|status|blame|ls-files|grep)\b"
    r"|(?:^|[\s;&|(])python[0-9.]*\s+-c\s+['\"]?(?:import\s+\S+;\s*)?print")
CLASSES = ("view", "test", "edit", "submit", "other")


def msg_text(m):
    c = m.get("content")
    if isinstance(c, list):
        c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c if isinstance(c, str) else ("" if c is None else str(c))


def command_of(text):
    """The shell command inside the mswea fence (empty string when the message has none)."""
    hits = FENCE.findall(text or "")
    return "\n".join(hits)


def classify(text):
    """-> (primary class, set of every class the message touches).

    Primary precedence: submit > edit > test > view > other.  `submit` is the fixed
    `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` line, so it never competes on content.
    A message with no fence at all (a malformed turn) is `other`.
    """
    cmd = command_of(text)
    if not cmd.strip():
        return "other", {"other"}
    if SUBMIT_RE.search(cmd):
        return "submit", {"submit"}          # the fixed harness ritual, nothing else
    tags = set()
    if EDIT_RE.search(cmd):
        tags.add("edit")
    if TEST_RE.search(cmd):
        tags.add("test")
    if VIEW_RE.search(cmd):
        tags.add("view")
    if not tags:
        tags.add("other")
    for c in ("submit", "edit", "test", "view", "other"):
        if c in tags:
            return c, tags
    return "other", tags


# ---------------------------------------------------------------- trainer import
def load_trainer():
    from importlib.machinery import SourceFileLoader
    import importlib.util

    def _load(path, name):
        ld = SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_loader(name, ld)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        ld.exec_module(mod)
        return mod

    base = _load(TC / "actor9_grpo_trainer.py", "actor9_grpo_trainer")
    prov = _load(TC / "actor9_grpo_trainer_prov.py", "_nm_prov")
    return base, prov


def render_spans(tok, msgs, maxlen):
    """`actor9_grpo_trainer.render` plus the per-assistant-message token spans.

    Same scan as the trainer (:952-982): render once, then walk the id sequence for
    `<|im_start|>assistant\\n` headers and mark up to and including the next `<|im_end|>`.
    Returns (n_ids, n_tok, [span_len per assistant message], truncated).
    """
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False,
                                   enable_thinking=False)
    ids = tok(text, add_special_tokens=False)["input_ids"]
    hdr = tok("<|im_start|>assistant\n", add_special_tokens=False)["input_ids"]
    im_end = tok.convert_tokens_to_ids("<|im_end|>")
    spans = []
    i, H = 0, len(hdr)
    while i <= len(ids) - H:
        if ids[i:i + H] == hdr:
            j = i + H
            while j < len(ids) and ids[j] != im_end:
                j += 1
            if j < len(ids):
                spans.append((i + H, j + 1))
                i = j + 1
                continue
        i += 1
    if not ids or not spans:
        return None
    trunc = len(ids) > maxlen
    n_ids = min(len(ids), maxlen)
    lens = []
    for (s, e) in spans:
        s2, e2 = min(s, n_ids), min(e, n_ids)
        lens.append(max(0, e2 - s2))
    n_tok = sum(lens)
    if n_tok == 0:
        return None
    return n_ids, n_tok, lens, trunc


# ---------------------------------------------------------------- stage: rollouts
_TOK = None
_BASE = None


def _init_worker():
    global _TOK, _BASE
    from transformers import AutoTokenizer
    _TOK = AutoTokenizer.from_pretrained(MODEL)
    _BASE, _ = load_trainer()


_SCAF_CACHE = {}


def _scaffold_tokens(text):
    """Cheap estimate of the fixed-format tokens of one assistant turn: the fence open and
    close lines, the `THOUGHT:` marker, and the `<|im_end|>` that closes the span."""
    global _SCAF_CACHE
    n = 1  # the <|im_end|> that the mask includes
    for lit, cond in (("```mswea_bash_command\n", "```mswea_bash_command" in (text or "")),
                      ("```\n", "```" in (text or "")),
                      ("THOUGHT:", (text or "").lstrip().startswith("THOUGHT:"))):
        if not cond:
            continue
        if lit not in _SCAF_CACHE:
            _SCAF_CACHE[lit] = len(_TOK(lit, add_special_tokens=False)["input_ids"])
        n += _SCAF_CACHE[lit]
    return n


def _render_one(args):
    """-> (base, dict) for one rollout; run in a worker process."""
    base_name, traj_path = args
    try:
        t = json.loads(pathlib.Path(traj_path).read_text(encoding="utf-8"))
    except Exception as e:
        return base_name, {"err": f"traj_read:{type(e).__name__}"}
    msgs = _BASE.clean_messages(t.get("messages") or [])
    out = render_spans(_TOK, msgs, MAXLEN)
    if out is None:
        return base_name, {"err": "render_none"}
    n_ids, n_tok, lens, trunc = out
    asst = [m for m in msgs if m.get("role") == "assistant"]
    if len(lens) != len(asst):                     # trainer prints the same warning
        return base_name, {"err": f"span_msg_mismatch:{len(lens)}/{len(asst)}"}
    prim = collections.Counter()
    multi = collections.Counter()
    scaf = 0
    n_act = collections.Counter()
    for m, L in zip(asst, lens):
        txt = msg_text(m)
        p, tags = classify(txt)
        prim[p] += L
        n_act[p] += 1
        for tg in tags:
            multi[tg] += L
        scaf += min(L, _scaffold_tokens(txt))
    return base_name, {"n_seqtok": n_ids, "n_tok": n_tok, "trunc": bool(trunc),
                       "n_asst": len(asst),
                       "prim": dict(prim), "multi": dict(multi), "scaffold": scaf,
                       "n_act": dict(n_act)}


def stage_rollouts(arms, cache, nproc):
    import multiprocessing as mp
    base, prov = load_trainer()

    blocks = {}
    for p in RES.glob("*.json"):
        m = NAME.match(p.stem)
        if not m or m["arm"] not in arms:
            continue
        blocks.setdefault((m["arm"], int(m["step"])), []).append(p.stem)
    # submission order: boundary first, then g (trainer submit_step :580 iterates pairs x G)
    for k in blocks:
        blocks[k].sort(key=lambda b: (b.rsplit("__g", 1)[0], int(b.rsplit("__g", 1)[1])))
    print(f"[nm] {len(blocks)} (arm, step) blocks, "
          f"{sum(len(v) for v in blocks.values())} rollouts", flush=True)

    all_rows = []
    todo = {}
    for (arm, step), names in sorted(blocks.items()):
        cfg = ARMS[arm]
        got = {}
        for b in names:
            try:
                got[b] = json.loads((RES / (b + ".json")).read_text(encoding="utf-8"))
            except Exception:
                pass
        if not got:
            continue
        rows, n_dead = base.rewards(
            names, got, cfg["G"], W_RESOLVE, False, 2.4, STEP_LIMIT,
            success_only=cfg["succ"], adv_scale=cfg["scale"], adv_fixed_scale=100.0,
            partial_credit=cfg["pc"],
            adv_center=(cfg["ctr"] if cfg["prov"] is None else "group"),
            mix_bare=cfg["mb"], shuf_slots=cfg["sh"])
        if cfg["prov"]:
            prov.subgroup_center(rows, cfg["G"], cfg["mb"], cfg["sh"],
                                 adv_scale=cfg["scale"], adv_fixed_scale=100.0,
                                 center=cfg["prov"])
        # the same rows under the OLD (non-success-only) reward, for prediction (iii)
        rows_old, _ = base.rewards(
            names, got, cfg["G"], W_RESOLVE, False, 2.4, STEP_LIMIT,
            success_only=False, adv_scale=cfg["scale"], adv_fixed_scale=100.0,
            partial_credit=0.0,
            adv_center=(cfg["ctr"] if cfg["prov"] is None else "group"),
            mix_bare=cfg["mb"], shuf_slots=cfg["sh"])
        a_old = {r["base"]: r.get("A", 0.0) for r in rows_old}
        for idx, r in enumerate(rows):
            g = int(r["base"].rsplit("__g", 1)[1])
            rec = {"arm": arm, "step": step, "base": r["base"], "group": r["group"],
                   "order": idx, "g": g,
                   "kind": base.vw_kind(g, cfg["G"], cfg["mb"], cfg["sh"]),
                   "present": bool(r["present"]), "A": r.get("A", 0.0), "R": r["R"],
                   "A_old": a_old.get(r["base"], 0.0),
                   "steps_used": r["steps_used"], "resolved_used": r["resolved_used"],
                   "dead_group": bool(r["dead_group"]),
                   "allfail_group": bool(r["allfail_group"]),
                   "exit": r.get("exit"), "n_steps_raw": r.get("n_steps"),
                   "n_chars": r.get("n_chars"),
                   "chain": r["group"].split(f"_s{step}_", 1)[-1].rsplit("__b", 1)[0],
                   "boundary": r["group"].split(f"_s{step}_", 1)[-1]}
            all_rows.append(rec)
            if rec["present"] and abs(rec["A"]) > 1e-9 and (r.get("traj")):
                todo[r["base"]] = r["traj"]
        print(f"[nm] {arm} s{step}: {len(got)}/{len(names)} present, "
              f"{sum(1 for r in rows if r['present'] and abs(r.get('A',0))>1e-9)} cand",
              flush=True)

    print(f"[nm] rendering {len(todo)} trajectories on {nproc} procs", flush=True)
    items = sorted(todo.items())
    rendered = {}
    if nproc > 1:
        with mp.get_context("fork").Pool(nproc, initializer=_init_worker) as pool:
            for i, (b, d) in enumerate(pool.imap_unordered(_render_one, items, chunksize=8)):
                rendered[b] = d
                if (i + 1) % 250 == 0:
                    print(f"[nm]   {i+1}/{len(items)}", flush=True)
    else:
        _init_worker()
        for i, it in enumerate(items):
            b, d = _render_one(it)
            rendered[b] = d
            if (i + 1) % 250 == 0:
                print(f"[nm]   {i+1}/{len(items)}", flush=True)

    for rec in all_rows:
        rec["render"] = rendered.get(rec["base"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(all_rows), encoding="utf-8")
    print(f"[nm] wrote {cache} ({len(all_rows)} rows)", flush=True)


# ---------------------------------------------------------------- stage: exam
EXAM_ARM = re.compile(r"^(?P<chain>.+?)__s(?P<slot>\d+)__(?P<arm>.+?)(?:_r(?P<rep>[23]))?$")


def exam_session_stats(d):
    """-> list of per-session-instance dicts from one exam run directory."""
    rj = d / "results.jsonl"
    if not rj.is_file():
        return []
    out = []
    for line in rj.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        out.append(row)
    return out


def traj_behaviour(path):
    """-> dict(n_asst, steps_before_first_edit, test_after_last_edit, n_edit, n_test,
    n_view, malformed) for one trajectory file."""
    try:
        t = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    msgs = t.get("messages") or []
    asst = [m for m in msgs if m.get("role") == "assistant"]
    cls = []
    for m in asst:
        p, tags = classify(msg_text(m))
        cls.append((p, tags))
    first_edit = next((i for i, (p, tg) in enumerate(cls) if "edit" in tg), None)
    last_edit = next((len(cls) - 1 - i for i, (p, tg) in enumerate(reversed(cls))
                      if "edit" in tg), None)
    test_after = None
    if last_edit is not None:
        test_after = any("test" in tg for (p, tg) in cls[last_edit + 1:])
    n_nofence = sum(1 for m in asst if not command_of(msg_text(m)).strip())
    return {"n_asst": len(asst),
            "any_test": any("test" in tg for (p, tg) in cls),
            "first_edit": first_edit,
            "steps_before_first_edit": (first_edit if first_edit is not None else len(cls)),
            "any_edit": first_edit is not None,
            "test_after_last_edit": test_after,
            "n_edit": sum(1 for p, tg in cls if "edit" in tg),
            "n_test": sum(1 for p, tg in cls if "test" in tg),
            "n_view": sum(1 for p, tg in cls if "view" in tg),
            "n_nofence": n_nofence}


def stage_exam(run_ids, arm_pats, cache):
    pats = [re.compile(p) for p in arm_pats]
    rows = []
    for rid in run_ids:
        root = RUNS / rid
        if not root.is_dir():
            print(f"[nm] !! no run dir {root}", flush=True)
            continue
        n = 0
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            m = EXAM_ARM.match(d.name)
            if not m:
                continue
            arm = m["arm"]
            if not any(p.search(arm) for p in pats):
                continue
            rep = int(m["rep"] or 1)
            for row in exam_session_stats(d):
                inst = row.get("instance") or ""
                tp = None
                for f in d.glob(f"traj_{row.get('order')}_*.json"):
                    tp = f
                    break
                beh = traj_behaviour(tp) if tp else None
                rows.append({"run": rid, "arm": arm, "rep": rep, "chain": m["chain"],
                             "slot": int(m["slot"]), "order": row.get("order"),
                             "instance": inst, "exit": row.get("exit"),
                             "resolved": bool(row.get("resolved")),
                             "gradable": bool(row.get("gradable")),
                             "n_steps": row.get("n_steps"),
                             "injected": bool(row.get("injected")),
                             "beh": beh})
                n += 1
        print(f"[nm] {rid}: {n} session-instances", flush=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows), encoding="utf-8")
    print(f"[nm] wrote {cache} ({len(rows)} rows)", flush=True)


# ---------------------------------------------------------------- statistics
def boot_ci(values, clusters, reps=2000, seed=0, stat=None):
    """Cluster bootstrap 95 % CI of the mean (or of `stat(list)`)."""
    if not values:
        return (float("nan"), float("nan"), float("nan"))
    by = collections.defaultdict(list)
    for v, c in zip(values, clusters):
        by[c].append(v)
    keys = sorted(by)
    f = stat or (lambda xs: sum(xs) / len(xs))
    point = f([v for k in keys for v in by[k]])
    if len(keys) < 2:
        return (point, float("nan"), float("nan"))
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        pick = [keys[rng.randrange(len(keys))] for _ in keys]
        xs = [v for k in pick for v in by[k]]
        if xs:
            out.append(f(xs))
    out.sort()
    lo = out[int(0.025 * len(out))]
    hi = out[min(len(out) - 1, int(0.975 * len(out)))]
    return (point, lo, hi)


def boot_ratio_ci(num, den, clusters, reps=2000, seed=0):
    """Cluster bootstrap CI of sum(num)/sum(|den|) -- the M statistic's shape."""
    by = collections.defaultdict(list)
    for a, b, c in zip(num, den, clusters):
        by[c].append((a, b))
    keys = sorted(by)
    sn = sum(a for k in keys for a, b in by[k])
    sd = sum(b for k in keys for a, b in by[k])
    point = sn / sd if sd else float("nan")
    if len(keys) < 2:
        return (point, float("nan"), float("nan"))
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        pick = [keys[rng.randrange(len(keys))] for _ in keys]
        a_ = sum(a for k in pick for a, b in by[k])
        b_ = sum(b for k in pick for a, b in by[k])
        if b_:
            out.append(a_ / b_)
    out.sort()
    if not out:
        return (point, float("nan"), float("nan"))
    return (point, out[int(0.025 * len(out))], out[min(len(out) - 1, int(0.975 * len(out)))])


def spearman(x, y):
    n = len(x)
    if n < 3:
        return float("nan")

    def rk(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rk(x), rk(y)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else float("nan")


def kish(weights):
    ws = [abs(w) for w in weights if abs(w) > 0]
    if not ws:
        return 0.0
    return (sum(ws) ** 2) / sum(w * w for w in ws)


def select_kept(cands, budget=TOK_BUDGET):
    """The trainer's token-budget pick (:1874-1881): stable sort by -|A|, greedy fill on
    the FULL sequence length; the first item is always kept (`if tot and ...`)."""
    b = sorted(cands, key=lambda r: -abs(r["A"]))
    keep, tot = [], 0
    for it in b:
        L = it["render"]["n_seqtok"]
        if tot and tot + L > budget:
            continue
        keep.append(it)
        tot += L
    return keep, tot


# ---------------------------------------------------------------- report
def read_tsv(arm):
    p = METRICS / f"actor27_{arm}.tsv"
    if not p.is_file():
        return {}
    lines = p.read_text(encoding="utf-8").splitlines()
    if not lines:
        return {}
    hdr = lines[0].split("\t")
    out = {}
    for ln in lines[1:]:
        f = ln.split("\t")
        if len(f) < 5:
            continue
        d = dict(zip(hdr, f))
        try:
            out[int(d["step"])] = d
        except Exception:
            pass
    return out


def f(x, n=3):
    try:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return "n/a"
        return f"{x:.{n}f}"
    except Exception:
        return "n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="report",
                    choices=("rollouts", "exam", "report", "all"))
    ap.add_argument("--arms", default=",".join(MAIN_ARMS + OLD_ARMS))
    ap.add_argument("--cache", default=os.environ.get(
        "RSM_NM_CACHE", "/tmp/claude-30026/neg_mass"))
    ap.add_argument("--nproc", type=int, default=3)
    ap.add_argument("--out", default=str(METRICS / "neg_mass_hypothesis_0920"))
    a = ap.parse_args()
    cache = pathlib.Path(a.cache)
    arms = [x for x in a.arms.split(",") if x in ARMS]

    if a.stage in ("rollouts", "all"):
        stage_rollouts(arms, cache / "rollouts.json", a.nproc)
    if a.stage in ("exam", "all"):
        stage_exam(["ft1_actor9", "ft1_grpo"],
                   [r"^mem_v5knote_a27.*_self$", r"^mem_v5knote_a27dvw2s0$",
                    r"^mem_v5knote_a27base$", r"^mem_v5knote_w27b$",
                    r"^mem_v5knote_a27dvw2s0_len98k$"],
                   cache / "exam.json")
    if a.stage in ("report", "all"):
        build_report(cache, pathlib.Path(a.out), arms)


# ---------------------------------------------------------------- the report body
def build_report(cache, outstem, arms):
    rows = json.loads((cache / "rollouts.json").read_text(encoding="utf-8"))
    exam = []
    ep = cache / "exam.json"
    if ep.is_file():
        exam = json.loads(ep.read_text(encoding="utf-8"))

    tsvs = {arm: read_tsv(arm) for arm in arms}
    # Only steps that the lane tsv records were actually turned into an update.  This
    # drops (a) the step whose rollouts are still being collected right now and (b) the
    # leftovers of the 2026-09-20 claim-lock incident (§8.93): the contaminated rows and
    # checkpoints were renamed `.dmg0920`, so the surviving tsv is exactly the clean set
    # (dvw6b keeps updates 0..7 and the redone step 8; prov6b keeps 0..4 and the redone 5).
    by_step = collections.defaultdict(list)
    dropped = collections.Counter()
    for r in rows:
        if r["step"] not in tsvs.get(r["arm"], {}):
            dropped[(r["arm"], r["step"])] += 1
            continue
        by_step[(r["arm"], r["step"])].append(r)
    if dropped:
        print("[nm] dropped (not in lane tsv): "
              + ", ".join(f"{a}s{s}:{n}" for (a, s), n in sorted(dropped.items())))

    J = {"generated": "2026-09-20", "arms": {}, "sections": {}}
    per_step = []          # csv rows

    # ---------------- A: reconstruction + mass balance --------------------------
    recon = []
    arm_step_rec = {}
    for (arm, step), rs in sorted(by_step.items()):
        cfg = ARMS[arm]
        pres = [r for r in rs if r["present"]]
        cands = [r for r in rs
                 if r["present"] and abs(r["A"]) > 1e-9 and not r["dead_group"]
                 and isinstance(r.get("render"), dict) and "n_tok" in r["render"]]
        n_render_fail = sum(1 for r in rs
                            if r["present"] and abs(r["A"]) > 1e-9 and not r["dead_group"]
                            and not (isinstance(r.get("render"), dict)
                                     and "n_tok" in r["render"]))
        kept, tot_seq = select_kept(cands)
        ntok = sum(k["render"]["n_tok"] for k in kept)
        arm_step_rec[(arm, step)] = {"pres": pres, "cands": cands, "kept": kept,
                                     "ntok": ntok, "n_seqtok": tot_seq,
                                     "n_render_fail": n_render_fail}
        recon.append((arm, step, len(cands), len(kept), ntok, tot_seq))

    match_rows = []
    for (arm, step), rec in sorted(arm_step_rec.items()):
        t = tsvs.get(arm, {}).get(step)
        if not t:
            continue
        try:
            got = (int(t["n_used"]), int(t["n_cand"]), int(t["n_tok"]), int(t["n_seqtok"]))
        except Exception:
            continue
        mine = (len(rec["kept"]), len(rec["cands"]), rec["ntok"], rec["n_seqtok"])
        match_rows.append({"arm": arm, "step": step, "tsv": got, "mine": mine,
                           "ok_used": got[0] == mine[0], "ok_cand": got[1] == mine[1],
                           "d_ntok": (mine[2] - got[2]) / got[2] if got[2] else float("nan"),
                           "d_seq": (mine[3] - got[3]) / got[3] if got[3] else float("nan")})
    J["sections"]["reconstruction"] = match_rows

    def mass(rs, key="A", tokkey="n_tok"):
        s = sum(r[key] * r["render"][tokkey] for r in rs)
        sa = sum(abs(r[key]) * r["render"][tokkey] for r in rs)
        return s, sa

    A_arm = {}
    for arm in arms:
        steps = sorted(s for (aa, s) in arm_step_rec if aa == arm)
        if not steps:
            continue
        rowsA = []
        cum = 0.0
        for st in steps:
            rec = arm_step_rec[(arm, st)]
            cands, kept = rec["cands"], rec["kept"]
            if not cands:
                continue
            s_all, sa_all = mass(cands)
            s_kept, sa_kept = mass(kept) if kept else (0.0, 0.0)
            # token-MEAN weighting: every trajectory weighted by |A| only
            sm_all = sum(r["A"] for r in cands)
            sma_all = sum(abs(r["A"]) for r in cands)
            sm_kept = sum(r["A"] for r in kept)
            sma_kept = sum(abs(r["A"]) for r in kept)
            neg_k = [r for r in kept if r["A"] < 0]
            pos_k = [r for r in kept if r["A"] > 0]
            # per-group covariance on the present rows of the step
            gcov, gcov_steps, nneg = [], [], 0
            gs = collections.defaultdict(list)
            for r in cands:
                gs[r["group"]].append(r)
            for gk, gr in gs.items():
                if len(gr) < 2:
                    continue
                aa = [x["A"] for x in gr]
                nn = [x["render"]["n_tok"] for x in gr]
                ss = [(x["steps_used"] or 0) for x in gr]
                ma, mn, ms = (sum(aa) / len(aa), sum(nn) / len(nn), sum(ss) / len(ss))
                cv = sum((p - ma) * (q - mn) for p, q in zip(aa, nn)) / len(aa)
                cs = sum((p - ma) * (q - ms) for p, q in zip(aa, ss)) / len(aa)
                gcov.append(cv)
                gcov_steps.append(cs)
                nneg += int(cv < 0)
            M_all = s_all / sa_all if sa_all else float("nan")
            M_kept = s_kept / sa_kept if sa_kept else float("nan")
            cum += (M_kept if M_kept == M_kept else 0.0)
            d = {"arm": arm, "step": st, "n_present": len(rec["pres"]),
                 "n_cand": len(cands), "n_kept": len(kept), "ntok": rec["ntok"],
                 "M_all": M_all, "M_kept": M_kept,
                 "M_mean_all": (sm_all / sma_all) if sma_all else float("nan"),
                 "M_mean_kept": (sm_kept / sma_kept) if sma_kept else float("nan"),
                 "share_neg_kept": (len(neg_k) / len(kept)) if kept else float("nan"),
                 "share_negmass_kept": (sum(abs(r["A"]) * r["render"]["n_tok"]
                                            for r in neg_k) / sa_kept) if sa_kept else float("nan"),
                 "share_negmass_mean_kept": (sum(abs(r["A"]) for r in neg_k) / sma_kept)
                                            if sma_kept else float("nan"),
                 "S_kept": s_kept, "Sabs_kept": sa_kept,
                 "len_neg_tok": (sum(r["render"]["n_tok"] for r in neg_k) / len(neg_k))
                                if neg_k else float("nan"),
                 "len_pos_tok": (sum(r["render"]["n_tok"] for r in pos_k) / len(pos_k))
                                if pos_k else float("nan"),
                 "len_neg_steps": (sum((r["steps_used"] or 0) for r in neg_k) / len(neg_k))
                                  if neg_k else float("nan"),
                 "len_pos_steps": (sum((r["steps_used"] or 0) for r in pos_k) / len(pos_k))
                                  if pos_k else float("nan"),
                 "mean_gcov_tok": (sum(gcov) / len(gcov)) if gcov else float("nan"),
                 "mean_gcov_steps": (sum(gcov_steps) / len(gcov_steps)) if gcov_steps
                                    else float("nan"),
                 "share_groups_negcov": (nneg / len(gcov)) if gcov else float("nan"),
                 "n_groups": len(gcov), "cum_M_kept": cum,
                 "n_render_fail": rec["n_render_fail"]}
            rowsA.append(d)
            per_step.append(d)
        if not rowsA:
            continue
        # pooled (mass-weighted) M with a cluster bootstrap over chains
        allc = [r for st in steps for r in arm_step_rec[(arm, st)]["cands"]]
        allk = [r for st in steps for r in arm_step_rec[(arm, st)]["kept"]]
        M_all_p = boot_ratio_ci([r["A"] * r["render"]["n_tok"] for r in allc],
                                [abs(r["A"]) * r["render"]["n_tok"] for r in allc],
                                [r["chain"] for r in allc])
        M_kept_p = boot_ratio_ci([r["A"] * r["render"]["n_tok"] for r in allk],
                                 [abs(r["A"]) * r["render"]["n_tok"] for r in allk],
                                 [r["chain"] for r in allk])
        M_mean_p = boot_ratio_ci([r["A"] for r in allk], [abs(r["A"]) for r in allk],
                                 [r["chain"] for r in allk])
        negk = [r for r in allk if r["A"] < 0]
        posk = [r for r in allk if r["A"] > 0]
        A_arm[arm] = {
            "gen": ARMS[arm]["gen"], "scale": ARMS[arm]["scale"], "lr": ARMS[arm]["lr"],
            "steps": rowsA, "n_steps": len(rowsA),
            "M_all_pooled": M_all_p, "M_kept_pooled": M_kept_p,
            "M_kept_mean_pooled": M_mean_p,
            "share_neg_kept": len(negk) / len(allk) if allk else float("nan"),
            "share_negmass_kept": (sum(abs(r["A"]) * r["render"]["n_tok"] for r in negk)
                                   / sum(abs(r["A"]) * r["render"]["n_tok"] for r in allk))
                                  if allk else float("nan"),
            "share_negmass_mean": (sum(abs(r["A"]) for r in negk)
                                   / sum(abs(r["A"]) for r in allk)) if allk else float("nan"),
            "len_neg_tok": statistics.mean([r["render"]["n_tok"] for r in negk]) if negk else None,
            "len_pos_tok": statistics.mean([r["render"]["n_tok"] for r in posk]) if posk else None,
            "len_neg_steps": statistics.mean([(r["steps_used"] or 0) for r in negk]) if negk else None,
            "len_pos_steps": statistics.mean([(r["steps_used"] or 0) for r in posk]) if posk else None,
            "S_kept": sum(r["A"] * r["render"]["n_tok"] for r in allk),
            "Sabs_kept": sum(abs(r["A"]) * r["render"]["n_tok"] for r in allk),
            "cum_M_kept": rowsA[-1]["cum_M_kept"],
            "share_groups_negcov": statistics.mean(
                [r["share_groups_negcov"] for r in rowsA
                 if r["share_groups_negcov"] == r["share_groups_negcov"]]) if rowsA else None,
            "n_kept": len(allk), "n_cand": len(allc),
        }
        # prediction (iii): the same kept rows re-scored under the OLD reward
        old_allfail = [r for r in allc if r["allfail_group"]]
        A_arm[arm]["allfail_S_new"] = sum(r["A"] * r["render"]["n_tok"] for r in old_allfail)
        A_arm[arm]["allfail_S_old"] = sum(r["A_old"] * r["render"]["n_tok"] for r in old_allfail)
        A_arm[arm]["allfail_n"] = len(old_allfail)
        A_arm[arm]["S_old_all"] = sum(r["A_old"] * r["render"]["n_tok"] for r in allc)
        A_arm[arm]["Sabs_old_all"] = sum(abs(r["A_old"]) * r["render"]["n_tok"] for r in allc)
    J["sections"]["A"] = A_arm

    # ---------------- B: class decomposition -----------------------------------
    B = {}
    for arm in arms:
        steps = sorted(s for (aa, s) in arm_step_rec if aa == arm)
        allk = [r for st in steps for r in arm_step_rec[(arm, st)]["kept"]]
        if not allk:
            continue
        out = {"n_kept": len(allk)}
        tot_abs = sum(abs(r["A"]) * r["render"]["n_tok"] for r in allk)
        for cl in CLASSES:
            num = [r["A"] * r["render"]["prim"].get(cl, 0) for r in allk]
            den = [abs(r["A"]) * r["render"]["prim"].get(cl, 0) for r in allk]
            ch = [r["chain"] for r in allk]
            pt, lo, hi = boot_ratio_ci(num, den, ch)
            out[cl] = {"S": sum(num), "Sabs": sum(den), "M": pt, "lo": lo, "hi": hi,
                       "tok": sum(r["render"]["prim"].get(cl, 0) for r in allk),
                       "share_tok": sum(r["render"]["prim"].get(cl, 0) for r in allk)
                                    / max(1, sum(r["render"]["n_tok"] for r in allk)),
                       "S_rel": sum(num) / tot_abs if tot_abs else float("nan")}
            numm = [r["A"] * r["render"]["multi"].get(cl, 0) for r in allk]
            denm = [abs(r["A"]) * r["render"]["multi"].get(cl, 0) for r in allk]
            p2, l2, h2 = boot_ratio_ci(numm, denm, ch)
            out[cl]["multi"] = {"S": sum(numm), "M": p2, "lo": l2, "hi": h2}
        num = [r["A"] * r["render"]["scaffold"] for r in allk]
        den = [abs(r["A"]) * r["render"]["scaffold"] for r in allk]
        pt, lo, hi = boot_ratio_ci(num, den, [r["chain"] for r in allk])
        out["scaffold"] = {"S": sum(num), "M": pt, "lo": lo, "hi": hi,
                           "tok": sum(r["render"]["scaffold"] for r in allk),
                           "share_tok": sum(r["render"]["scaffold"] for r in allk)
                                        / max(1, sum(r["render"]["n_tok"] for r in allk))}
        B[arm] = out
    J["sections"]["B"] = B

    # ---------------- E: counterfactual weighting schemes -----------------------
    E = {}
    for arm in arms:
        steps = sorted(s for (aa, s) in arm_step_rec if aa == arm)
        if not steps:
            continue
        sch = {k: {c: 0.0 for c in CLASSES} for k in
               ("tok_sum", "traj_mean", "pos_tokmean", "pos_toksum")}
        for k in sch:
            sch[k]["_generic"] = 0.0
            sch[k]["_kish"] = []
            sch[k]["_zero_steps"] = 0
            sch[k]["_scaffold"] = 0.0
        n_steps_used = 0
        for st in steps:
            kept = arm_step_rec[(arm, st)]["kept"]
            if not kept:
                continue
            n_steps_used += 1
            ntok = sum(r["render"]["n_tok"] for r in kept) or 1
            pos = [r for r in kept if r["A"] > 0]
            # (1) current: coefficient of trajectory i is A_i (token-sum, /ntok)
            w1 = {r["base"]: r["A"] / ntok for r in kept}
            # (2) sequence-mean normalisation, symmetric A: A_i / n_i, averaged over
            #     trajectories (the usual "per-sequence mean" GRPO normaliser)
            w2 = {r["base"]: r["A"] / (r["render"]["n_tok"] * len(kept)) for r in kept}
            # (3a) positive-only with sequence-mean normalisation
            w3 = {r["base"]: (r["A"] / (r["render"]["n_tok"] * max(1, len(pos))))
                  if r["A"] > 0 else 0.0 for r in kept}
            # (3b) positive-only, token-sum (for the coefficient algebra)
            ntok_pos = sum(r["render"]["n_tok"] for r in pos) or 1
            w4 = {r["base"]: (r["A"] / ntok_pos) if r["A"] > 0 else 0.0 for r in kept}
            for nm, w in (("tok_sum", w1), ("traj_mean", w2),
                          ("pos_tokmean", w3), ("pos_toksum", w4)):
                for r in kept:
                    for c in CLASSES:
                        sch[nm][c] += w[r["base"]] * r["render"]["prim"].get(c, 0)
                    sch[nm]["_scaffold"] += w[r["base"]] * r["render"]["scaffold"]
                    # a generic token class at constant rate r per agent step:
                    # coefficient = sum_i w_i * n_steps_i  (rate factored out)
                    sch[nm]["_generic"] += w[r["base"]] * (r["steps_used"] or 0)
                sch[nm]["_kish"].append(kish([w[r["base"]] * r["render"]["n_tok"]
                                              for r in kept]))
                if not pos:
                    sch[nm]["_zero_steps"] += 1
        for nm in sch:
            sch[nm]["_kish_mean"] = (statistics.mean(sch[nm]["_kish"])
                                     if sch[nm]["_kish"] else float("nan"))
            sch[nm].pop("_kish")
        # the "= 0 exactly" check: sum_i A_i over the full group set vs over the kept subset
        allc = [r for st in steps for r in arm_step_rec[(arm, st)]["cands"]]
        allk = [r for st in steps for r in arm_step_rec[(arm, st)]["kept"]]
        allp = [r for st in steps for r in arm_step_rec[(arm, st)]["pres"]]
        E[arm] = {"schemes": sch, "n_steps": n_steps_used,
                  "n_zero_pos_steps": sch["pos_tokmean"]["_zero_steps"],
                  "sumA_present": sum(r["A"] for r in allp),
                  "sumA_cand": sum(r["A"] for r in allc),
                  "sumA_kept": sum(r["A"] for r in allk),
                  "sumAbs_kept": sum(abs(r["A"]) for r in allk),
                  "sumAn_kept": sum(r["A"] * r["render"]["n_tok"] for r in allk),
                  "sumAn_pos_kept": sum(r["A"] * r["render"]["n_tok"]
                                        for r in allk if r["A"] > 0)}
    J["sections"]["E"] = E

    # ---------------- D: dvw6b format collapse ---------------------------------
    D = {"dvw6b": {}, "prov6b": {}}
    raw_step = collections.defaultdict(list)
    for r in rows:
        raw_step[(r["arm"], r["step"])].append(r)
    for arm in ("dvw6b", "prov6b"):
        steps = sorted(s for (aa, s) in raw_step if aa == arm)
        seq = []
        for st in steps:
            rs = [r for r in raw_step[(arm, st)] if r["present"]]
            if len(rs) < 8:               # first-attempt leftovers / just-started step
                continue
            in_tsv = st in tsvs.get(arm, {})
            if not rs:
                continue
            q3 = sum(1 for r in rs if (r["n_steps_raw"] or 999) <= 3) / len(rs)
            ex = collections.Counter(str(r["exit"]) for r in rs)
            kinds = collections.defaultdict(lambda: [0, 0])
            for r in rs:
                kinds[r["kind"]][0] += int(str(r["exit"]) == "RepeatedFormatError")
                kinds[r["kind"]][1] += 1
            seq.append({"step": st, "n": len(rs), "quick3": q3, "in_tsv": in_tsv,
                        "rfe": ex.get("RepeatedFormatError", 0) / len(rs),
                        "exits": dict(ex),
                        "steps_mean": statistics.mean([(r["steps_used"] or 0) for r in rs]),
                        "resolve": statistics.mean([(r["resolved_used"] or 0) for r in rs]),
                        "by_kind_rfe": {k: (v[0] / v[1] if v[1] else None, v[1])
                                        for k, v in kinds.items()}})
        D[arm] = {"by_step": seq}
    D["dvw6b"]["malformed"] = malformed_anatomy("dvw6b", [4, 5, 6, 7, 8, 9])
    J["sections"]["D"] = D

    # ---------------- C: test-frame behaviour ----------------------------------
    C = exam_tables(exam, A_arm)
    J["sections"]["C"] = C

    # ---------------- explicit predictions -------------------------------------
    P = {}
    sig = {a: (A_arm[a]["M_kept_pooled"][2] < 0) for a in A_arm}     # CI upper < 0
    P["i"] = {"per_arm": {a: {"M": A_arm[a]["M_kept_pooled"][0],
                              "ci": A_arm[a]["M_kept_pooled"][1:],
                              "neg": A_arm[a]["M_kept_pooled"][0] < 0,
                              "neg_sig": sig[a]} for a in A_arm},
              "n_neg": sum(1 for a in A_arm if A_arm[a]["M_kept_pooled"][0] < 0),
              "n_arms": len(A_arm),
              "n_neg_sig": sum(1 for a in sig if sig[a])}
    g3 = [a for a in A_arm if ARMS[a]["gen"] == 3]
    g4 = [a for a in A_arm if ARMS[a]["gen"] == 4]
    # pooled M per generation with a step-level bootstrap
    def pooled_gen(gen):
        rs = [r for a in A_arm if ARMS[a]["gen"] == gen for r in A_arm[a]["steps"]]
        num = [r["S_kept"] for r in rs]
        den = [r["Sabs_kept"] for r in rs]
        return boot_ratio_ci(num, den, [f"{r['arm']}s{r['step']}" for r in rs]), len(rs)
    P["ii"] = {"gen3": pooled_gen(3), "gen4": pooled_gen(4), "gen5": pooled_gen(5),
               "gen6": pooled_gen(6), "gen1": pooled_gen(1), "gen2": pooled_gen(2),
               "g3_arms": {a: A_arm[a]["M_kept_pooled"][0] for a in g3},
               "g4_arms": {a: A_arm[a]["M_kept_pooled"][0] for a in g4}}
    P["iii"] = {a: {"n_allfail": A_arm[a]["allfail_n"],
                    "S_new": A_arm[a]["allfail_S_new"],
                    "S_old": A_arm[a]["allfail_S_old"],
                    "succ_only": ARMS[a]["succ"],
                    "pc": ARMS[a]["pc"]} for a in A_arm}
    P["iv"] = (C.get("dose") or {})
    nm_arms = [a for a in A_arm if a.startswith("nomem")]
    mm_arms = [a for a in A_arm if a.startswith(("dvw", "prov", "mem"))]
    P["v"] = {"nomem_absM": {a: abs(A_arm[a]["M_kept_pooled"][0]) for a in nm_arms},
              "mem_absM": {a: abs(A_arm[a]["M_kept_pooled"][0]) for a in mm_arms},
              "nomem_mean": statistics.mean([abs(A_arm[a]["M_kept_pooled"][0])
                                             for a in nm_arms]) if nm_arms else None,
              "mem_mean": statistics.mean([abs(A_arm[a]["M_kept_pooled"][0])
                                           for a in mm_arms]) if mm_arms else None}
    J["sections"]["predictions"] = P

    # ---------------- write --------------------------------------------------
    outstem.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS / "neg_mass_M_by_step_0920.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["arm", "step", "M_all", "M_kept", "n_present", "n_kept",
                    "share_groups_negcov", "cum_M_kept"])
        for d in per_step:
            w.writerow([d["arm"], d["step"], f(d["M_all"], 4), f(d["M_kept"], 4),
                        d["n_present"], d["n_kept"], f(d["share_groups_negcov"], 3),
                        f(d["cum_M_kept"], 4)])
    outstem.with_suffix(".json").write_text(json.dumps(J, indent=1, default=str),
                                            encoding="utf-8")
    md = render_md(J, arms)
    outstem.with_suffix(".md").write_text(md, encoding="utf-8")
    print(md)


FOUND_RE = re.compile(r"found (\d+)")


def malformed_anatomy(arm, steps):
    """What the RepeatedFormatError rollouts actually look like.

    mini-swe-agent does NOT keep the offending assistant turn: on a format failure it
    appends only a `Format error: ...` user message and re-queries, so the malformed text
    is not recoverable from the stored trajectory.  What is recoverable is the harness's
    own complaint (which says how many action blocks it found), the number of API calls
    that were burned, and the last turn that WAS well formed.
    """
    out = {}
    for st in steps:
        recs = {"n_rfe": 0, "api_calls": collections.Counter(),
                "found": collections.Counter(), "n_asst_kept": collections.Counter(),
                "examples": []}
        for p in sorted(RES.glob(f"{arm}_s{st}_*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(d.get("exit")) != "RepeatedFormatError" or not d.get("traj"):
                continue
            try:
                t = json.loads(pathlib.Path(d["traj"]).read_text(encoding="utf-8"))
            except Exception:
                continue
            recs["n_rfe"] += 1
            recs["api_calls"][t.get("info", {}).get("model_stats", {}).get("api_calls")] += 1
            asst = [m for m in (t.get("messages") or []) if m.get("role") == "assistant"]
            recs["n_asst_kept"][len(asst)] += 1
            for m in (t.get("messages") or []):
                if m.get("role") == "user" and "Format error" in msg_text(m):
                    mm = FOUND_RE.search(msg_text(m))
                    recs["found"][mm.group(1) if mm else "other"] += 1
            if asst and len(recs["examples"]) < 3:
                c = msg_text(asst[-1])
                recs["examples"].append({"base": p.stem, "n_asst": len(asst),
                                         "n_steps_raw": d.get("n_steps_raw"),
                                         "last_wellformed": "\n".join(
                                             c.splitlines()[:15])[:1200]})
        # runaway-thinking check: over ALL rollouts of the step, how long is an assistant
        # turn and how often does one carry no fenced action at all
        nch, nof, nmsg = [], 0, 0
        for p in sorted(RES.glob(f"{arm}_s{st}_*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                t = json.loads(pathlib.Path(d["traj"]).read_text(encoding="utf-8"))
            except Exception:
                continue
            for m in (t.get("messages") or []):
                if m.get("role") != "assistant":
                    continue
                c = msg_text(m)
                nch.append(len(c))
                nmsg += 1
                nof += int(not command_of(c).strip())
        recs["mean_asst_chars"] = statistics.mean(nch) if nch else None
        recs["p90_asst_chars"] = (sorted(nch)[int(0.9 * len(nch))] if nch else None)
        recs["n_asst_msgs"] = nmsg
        recs["share_turns_no_fence"] = (nof / nmsg) if nmsg else None
        recs["api_calls"] = dict(recs["api_calls"])
        recs["found"] = dict(recs["found"])
        recs["n_asst_kept"] = dict(sorted(recs["n_asst_kept"].items()))
        out[st] = recs
    return out


def exam_tables(exam, A_arm):
    """Section C: exit shares, investigation depth, untested submits, resolve; plus the
    dose-ordering rank correlations."""
    if not exam:
        return {"note": "exam cache empty"}
    by_arm = collections.defaultdict(list)
    for r in exam:
        by_arm[r["arm"]].append(r)
    reps_ok = {}
    for arm, rs in by_arm.items():
        reps = collections.Counter(r["rep"] for r in rs)
        reps_ok[arm] = dict(reps)

    out = {"reps": reps_ok, "arms": {}}
    for arm, rs in sorted(by_arm.items()):
        n = len(rs)
        if not n:
            continue
        ex = collections.Counter(str(r["exit"]) for r in rs)
        fmt = sum(v for k, v in ex.items() if "Format" in k)
        ctx = sum(v for k, v in ex.items() if "ContextWindow" in k)
        lim = sum(v for k, v in ex.items() if "LimitsExceeded" in k)
        sub = ex.get("Submitted", 0)
        oth = n - fmt - ctx - lim - sub
        beh = [r for r in rs if r.get("beh")]
        inv = [r["beh"]["steps_before_first_edit"] for r in beh if r["beh"]["any_edit"]]
        subs = [r for r in beh if str(r["exit"]) == "Submitted" and r["beh"]["any_edit"]
                and r["beh"]["test_after_last_edit"] is not None]
        untested = [1.0 - float(r["beh"]["test_after_last_edit"]) for r in subs]
        ch = [r["chain"] for r in subs]
        chi = [r["chain"] for r in beh if r["beh"]["any_edit"]]
        notest = [1.0 - float(r["beh"]["any_test"]) for r in beh
                  if str(r["exit"]) == "Submitted"]
        chn = [r["chain"] for r in beh if str(r["exit"]) == "Submitted"]
        out["arms"][arm] = {
            "n": n, "n_reps": len(set(r["rep"] for r in rs)),
            "resolve": statistics.mean([float(r["resolved"]) for r in rs]),
            "resolve_ci": boot_ci([float(r["resolved"]) for r in rs],
                                  [r["chain"] for r in rs]),
            "exit_shares": {k: v / n for k, v in ex.most_common()},
            "share_format_exit": fmt / n, "share_ctx_exit": ctx / n,
            "share_limit_exit": lim / n, "share_submitted": sub / n,
            "share_other_exit": oth / n,
            "steps_mean": statistics.mean([r["n_steps"] or 0 for r in rs]),
            "inv_steps": statistics.mean(inv) if inv else float("nan"),
            "inv_steps_ci": boot_ci(inv, chi) if inv else None,
            "n_inv": len(inv),
            "untested_submit": statistics.mean(untested) if untested else float("nan"),
            "untested_ci": boot_ci(untested, ch) if untested else None,
            "n_sub": len(subs),
            "no_test_at_all": statistics.mean(notest) if notest else float("nan"),
            "no_test_ci": boot_ci(notest, chn) if notest else None,
            "n_sub_all": len(notest),
            "n_edit_mean": statistics.mean([r["beh"]["n_edit"] for r in beh]) if beh else None,
            "n_test_mean": statistics.mean([r["beh"]["n_test"] for r in beh]) if beh else None,
            "n_view_mean": statistics.mean([r["beh"]["n_view"] for r in beh]) if beh else None,
        }

    # paired differences from the right comparator, on the common (chain, slot, order) cells
    def paired(arm, comp, field):
        A = {}
        for r in by_arm.get(arm, []):
            if r.get("beh"):
                A.setdefault((r["chain"], r["slot"], r["order"]), []).append(r)
        Bc = {}
        for r in by_arm.get(comp, []):
            if r.get("beh"):
                Bc.setdefault((r["chain"], r["slot"], r["order"]), []).append(r)
        keys = sorted(set(A) & set(Bc))
        d, cl = [], []
        for k in keys:
            def val(rs):
                if field == "inv":
                    v = [x["beh"]["steps_before_first_edit"] for x in rs
                         if x["beh"]["any_edit"]]
                elif field == "untested":
                    v = [1.0 - float(x["beh"]["test_after_last_edit"]) for x in rs
                         if str(x["exit"]) == "Submitted" and x["beh"]["any_edit"]
                         and x["beh"]["test_after_last_edit"] is not None]
                elif field == "notest":
                    v = [1.0 - float(x["beh"]["any_test"]) for x in rs
                         if str(x["exit"]) == "Submitted"]
                elif field == "resolve":
                    v = [float(x["resolved"]) for x in rs]
                else:
                    v = [float(x["n_steps"] or 0) for x in rs]
                return statistics.mean(v) if v else None
            va, vb = val(A[k]), val(Bc[k])
            if va is None or vb is None:
                continue
            d.append(va - vb)
            cl.append(k[0])
        if not d:
            return None
        pt, lo, hi = boot_ci(d, cl)
        return {"n_cells": len(d), "delta": pt, "lo": lo, "hi": hi}

    COMP98 = "mem_v5knote_w27b"
    COMP64 = "mem_v5knote_a27dvw2s0"
    out["paired"] = {}
    for arm in sorted(by_arm):
        if not arm.endswith("_self"):
            continue
        comp = COMP98 if COMP98 in by_arm else COMP64
        row = {}
        for field in ("inv", "untested", "notest", "resolve", "steps"):
            row[field] = paired(arm, comp, field)
        row["comparator"] = comp
        out["paired"][arm] = row

    # dose ordering: cumulative |negative mass| received up to the checkpoint
    dose, beh_inv, beh_unt, beh_fmt, beh_res, labels = [], [], [], [], [], []
    beh_nt = []
    for arm in sorted(by_arm):
        m = re.match(r"^mem_v5knote_a27(?P<tr>[a-z0-9]+?)s(?P<n>\d+)_self$", arm)
        if not m:
            continue
        tr, k = m["tr"], int(m["n"])
        if tr not in A_arm:
            continue
        rowsA = A_arm[tr]["steps"]
        cum = sum(-min(0.0, r["S_kept"]) for r in rowsA if r["step"] < k)
        cumM = sum(r["M_kept"] for r in rowsA if r["step"] < k
                   and r["M_kept"] == r["M_kept"])
        # cumulative KL from the lane tsv -- a scale-free "how far did the policy move"
        _t = read_tsv(tr)
        cumKL = 0.0
        for st_, d_ in _t.items():
            if st_ < k:
                try:
                    cumKL += float(d_.get("KL") or 0.0)
                except Exception:
                    pass
        st = out["arms"].get(arm)
        if not st:
            continue
        dose.append(cum)
        labels.append({"arm": arm, "train_arm": tr, "ckpt": k,
                       "cum_neg_mass": cum, "cum_M": cumM, "cum_KL": cumKL,
                       "inv": st["inv_steps"], "untested": st["untested_submit"],
                       "no_test": st.get("no_test_at_all"),
                       "fmt": st["share_format_exit"], "resolve": st["resolve"]})
        beh_inv.append(st["inv_steps"])
        beh_unt.append(st["untested_submit"])
        beh_nt.append(st.get("no_test_at_all", float("nan")))
        beh_fmt.append(st["share_format_exit"])
        beh_res.append(st["resolve"])
    # dose is monotone in checkpoint index within an arm, so the pooled correlation is
    # partly "more training".  The cross-arm correlation AT A FIXED checkpoint is the part
    # that is about the dose rather than about the step count.
    byck = collections.defaultdict(list)
    for x in labels:
        byck[x["ckpt"]].append(x)
    ck_rho = {}
    for k, xs in sorted(byck.items()):
        if len(xs) < 4:
            continue
        ck_rho[k] = {"n_arms": len(xs),
                     "rho_inv": spearman([-z["cum_M"] for z in xs],
                                         [z["inv"] for z in xs]),
                     "rho_notest": spearman([-z["cum_M"] for z in xs],
                                            [z.get("no_test") or 0.0 for z in xs]),
                     "rho_resolve": spearman([-z["cum_M"] for z in xs],
                                             [z["resolve"] for z in xs])}
    out["dose"] = {"points": labels, "by_ckpt": ck_rho,
                   "rho_inv": spearman(dose, beh_inv) if len(dose) > 2 else None,
                   "rho_untested": spearman(dose, beh_unt) if len(dose) > 2 else None,
                   "rho_notest": spearman(dose, beh_nt) if len(dose) > 2 else None,
                   "rho_inv_cumM": spearman([-x["cum_M"] for x in labels], beh_inv)
                                   if len(dose) > 2 else None,
                   "rho_notest_cumM": spearman([-x["cum_M"] for x in labels], beh_nt)
                                      if len(dose) > 2 else None,
                   "rho_inv_cumKL": spearman([x["cum_KL"] for x in labels], beh_inv)
                                    if len(dose) > 2 else None,
                   "rho_notest_cumKL": spearman([x["cum_KL"] for x in labels], beh_nt)
                                       if len(dose) > 2 else None,

                   "rho_format": spearman(dose, beh_fmt) if len(dose) > 2 else None,
                   "rho_resolve": spearman(dose, beh_res) if len(dose) > 2 else None,
                   "n": len(dose)}
    return out


def render_md(J, arms):
    A = J["sections"]["A"]
    B = J["sections"]["B"]
    E = J["sections"]["E"]
    D = J["sections"]["D"]
    C = J["sections"].get("C") or {}
    L = []
    w = L.append

    w("# Negative-advantage token mass in the actor27 GRPO updates (2026-09-20)\n")
    w("_Script_: `train-corpus/neg_mass_hypothesis.py`.  Read-only reconstruction of every "
      "stored update from `actor27_rollout_results/` with the trainer's own "
      "`rewards()` / `subgroup_center()` / token-budget pick and the real Qwen3.8-27B "
      "tokenizer.\n")

    w("## VERDICT\n")
    w("""
1. **H is supported on the mass side (A).** The signed mass `Σ A·n_tok` the gradient sees
   is negative in 16/16 arms (95 % CI clears 0 in 8/16). Kept `A<0` trajectories are
   36-56 % of the batch but carry **52-77 %** of the `|A|·n` mass; they are 1.3-2.1x
   longer in tokens and 1.7-3.0x longer in agent steps than the `A>0` ones, and 61-88 %
   of groups have `Cov(A, n_tok) < 0`.
2. **There are two amplifiers, not one.** `|M_kept| > |M_all|` in 16/16 arms (nomem3s
   -0.19 -> -0.54, dvw3s -0.14 -> -0.36): the `--tok-budget` top-|A| pick concentrates the
   negative mass on top of what the token-sum normalisation alone does.
3. **B is supported for reading and for the format scaffold, weakly for testing.** Net
   mass is negative for view/search in 15/16 arms (CI clears 0 in 9) and for the scaffold
   in 16/16 (CI in 10); `test` is negative in 11/16 but holds only 5-6 % of the tokens and
   clears its CI in 2. `submit` is the one class with net POSITIVE mass (14/16, CI in 5).
   The gradient reads "read less, submit sooner"; "test less" is directional only.
4. **Predictions:** (i) yes in sign, 8/16 significantly. (ii) yes -- pooled M_kept gen 3
   -0.385 [-0.468, -0.302] vs gen 4 -0.175 [-0.243, -0.106]. (iii) yes -- under
   `--reward-success-only` all-fail groups are identically R=0 so `Σ A n = 0` exactly
   (0 all-fail candidates in gens 3-4), while the same rollouts under the old reward give
   -6.25e5 of nomem's -9.24e5 total (68 %). (v) **no** -- |M| is *larger* for the
   no-memory arms (mean 0.326 vs 0.198).
5. **C: behaviour moves the right way, but the dose ordering is not established.** Against
   the untrained 98K comparator, investigation steps fall 0.6-1.5 (CI clears 0 in most
   checkpoints), no-test-at-all rises 0.02-0.07, resolve is flat to -0.03, total steps
   -1 to -7. Dose rank correlations are the predicted sign (inv -0.34, no-test +0.47) but
   dose is collinear with the checkpoint index; at a fixed checkpoint the cross-arm rho is
   -0.67/+0.69 at ckpt 4 and inconsistent at 8/12/14. Directional, not established.
6. **D: dvw6b's collapse is NOT the same mechanism at higher lr.** The collapse is real and
   monotone (quick3 0.062 -> 0.141 -> 0.484, RepeatedFormatError 0.078 -> 0.203 -> 0.812,
   all 198 harness complaints are "found 0 actions", foil 1.00 > real 0.88 > bare 0.72)
   and it transfers to the test frame (89 % format exits, resolve 0.089 vs 0.331). But
   dvw6b carries the *least* negative mass of all 16 arms (M_kept -0.048 [-0.276, +0.201],
   cumulative M +0.10), prov6b at the same lr shows zero format errors through step 6, and
   the collapse self-corrects: one more update takes step 9 back to 0 format errors.
7. **E: the evidence favours (3) positive-only, not (2).** Trajectory-mean does NOT zero
   the generic-token coefficient -- it stays negative in 16/16 arms -- because the top-|A|
   pick makes `Σ_kept A != 0` (-116 nomem3s, -76 dvw3s) even though `Σ A` over all present
   rows is 0 to 6e-14. Positive-only flips view, test and scaffold to net positive in
   16/16 arms and costs almost nothing: no step in any arm (0/205) loses all its
   positive-advantage trajectories and the mean Kish n_eff is unchanged (10.3 -> 10.2).
""".strip())
    w("")

    P = J["sections"].get("predictions") or {}
    if P:
        w("\n## Pre-registered predictions\n")
        p1 = P["i"]
        w(f"**(i) M significantly negative in every arm** -- M_kept < 0 in "
          f"{p1['n_neg']}/{p1['n_arms']} arms; the 95 % CI clears 0 in "
          f"{p1['n_neg_sig']}/{p1['n_arms']}.")
        w("")
        w("| arm | M_kept | 95% CI | negative | CI clears 0 |")
        w("|---|---|---|---|---|")
        for a in arms:
            if a not in p1["per_arm"]:
                continue
            d = p1["per_arm"][a]
            w(f"| {a} | {f(d['M'])} | [{f(d['ci'][0])}, {f(d['ci'][1])}] | "
              f"{'yes' if d['neg'] else 'NO'} | {'yes' if d['neg_sig'] else 'no'} |")
        w("")
        p2 = P["ii"]
        w("**(ii) group-sd arms (gen 3) more negative than fixed-scale arms (gen 4+)**")
        w("")
        w("| generation | pooled M_kept | 95% CI | steps |")
        w("|---|---|---|---|")
        for g in (1, 2, 3, 4, 5, 6):
            k = f"gen{g}"
            if k not in p2 or not p2[k][1]:
                continue
            (pt, lo, hi), n = p2[k]
            w(f"| {g} | {f(pt)} | [{f(lo)}, {f(hi)}] | {n} |")
        w("")
        p5 = P["v"]
        w(f"**(v) no-memory arms have smaller |M|** -- mean |M_kept| nomem* "
          f"{f(p5['nomem_mean'])} vs memory arms {f(p5['mem_mean'])}.")
        w("")

    w("\n## Reconstruction check (vs the lane tsv)\n")
    mr = J["sections"]["reconstruction"]
    ok_u = sum(1 for r in mr if r["ok_used"])
    ok_c = sum(1 for r in mr if r["ok_cand"])
    dn = [abs(r["d_ntok"]) for r in mr if r["d_ntok"] == r["d_ntok"]]
    w(f"- {len(mr)} (arm, step) pairs compared against the lane tsv's "
      f"`n_cand` / `n_used` / `n_tok` / `n_seqtok`; `n_used` exact on {ok_u}, "
      f"`n_cand` exact on {ok_c}.")
    if dn:
        w(f"- |relative n_tok error| median {statistics.median(dn):.4f}, "
          f"max {max(dn):.4f}.")
    w("")
    w("| arm | steps | n_used exact | n_cand exact | n_tok exact | median |rel n_tok err| |")
    w("|---|---|---|---|---|---|")
    by = collections.defaultdict(list)
    for r in mr:
        by[r["arm"]].append(r)
    for arm in arms:
        rs = by.get(arm)
        if not rs:
            continue
        e = [abs(r["d_ntok"]) for r in rs if r["d_ntok"] == r["d_ntok"]]
        w(f"| {arm} | {len(rs)} | {sum(1 for r in rs if r['ok_used'])} | "
          f"{sum(1 for r in rs if r['ok_cand'])} | "
          f"{sum(1 for r in rs if abs(r['d_ntok']) < 1e-12)} | "
          f"{statistics.median(e):.4f} |")
    w("")
    w("A mismatch means the stored result set is no longer what the trainer harvested: a "
      "straggler that timed out at harvest (imputed, `present=False`) but whose worker "
      "later wrote its result. Those steps keep the same rollouts but a different "
      "token-budget pick, so their `M_kept` is a close reconstruction, not the exact "
      "update. `M_all` does not depend on the pick at all.")
    w("")

    w("\n## A. Mass balance per update (pooled per arm, kept subset)\n")
    w("| arm | gen | scale | steps | kept | %kept A<0 | %|A|n mass neg | %|A| mass neg (traj-mean) "
      "| M_all | M_kept [95% CI] | len A<0 (tok/steps) | len A>0 (tok/steps) | %groups Cov<0 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for arm in arms:
        if arm not in A:
            continue
        d = A[arm]
        mk = d["M_kept_pooled"]
        ma = d["M_all_pooled"]
        w(f"| {arm} | {d['gen']} | {d['scale']} | {d['n_steps']} | {d['n_kept']} | "
          f"{100*d['share_neg_kept']:.1f} | {100*d['share_negmass_kept']:.1f} | "
          f"{100*d['share_negmass_mean']:.1f} | {f(ma[0])} | "
          f"{f(mk[0])} [{f(mk[1])}, {f(mk[2])}] | "
          f"{d['len_neg_tok']:.0f}/{d['len_neg_steps']:.1f} | "
          f"{d['len_pos_tok']:.0f}/{d['len_pos_steps']:.1f} | "
          f"{100*d['share_groups_negcov']:.0f} |")
    w("")
    w("`M = Σ A_i n_i / Σ|A_i| n_i`.  `M_kept` under trajectory-MEAN weighting "
      "(= Σ A_i / Σ|A_i|, the quantity that would replace M if the loss divided each "
      "trajectory by its own length):")
    w("")
    w("| arm | M_kept (token-sum) | M_kept (traj-mean) | Σ A n (kept) | Σ|A| n (kept) | cum M |")
    w("|---|---|---|---|---|---|")
    for arm in arms:
        if arm not in A:
            continue
        d = A[arm]
        w(f"| {arm} | {f(d['M_kept_pooled'][0])} | {f(d['M_kept_mean_pooled'][0])} "
          f"[{f(d['M_kept_mean_pooled'][1])}, {f(d['M_kept_mean_pooled'][2])}] | "
          f"{d['S_kept']:.3e} | {d['Sabs_kept']:.3e} | {f(d['cum_M_kept'],2)} |")

    w("\n### All-fail groups: old reward vs success-only (prediction iii)\n")
    w("| arm | all-fail rollouts | Σ A n (success-only) | Σ A n (old reward, same rollouts) |")
    w("|---|---|---|---|")
    for arm in arms:
        if arm not in A:
            continue
        d = A[arm]
        w(f"| {arm} | {d['allfail_n']} | {d['allfail_S_new']:.3e} | {d['allfail_S_old']:.3e} |")
    w("")
    w("Under `--reward-success-only` an all-fail group has R = 0 for every member, so "
      "A = 0 for every member and the group is dropped before rendering: `Σ A n = 0` by "
      "construction, and `n_allfail = 0` above means exactly that. The non-zero rows are "
      "the generation-1 arms (no `--reward-success-only`: failures carry R = -steps) and "
      "the generation-5/6 arms, where `--partial-credit 50` puts variance back into "
      "all-fail groups.")

    w("\n### Per-step M (the arms where the sign moves)\n")
    w("| arm | step | n_cand | n_kept | M_all | M_kept | mean Cov(A,n_tok) | %groups Cov<0 "
      "| mean steps A<0 | mean steps A>0 |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for arm in ("dvw6b", "prov6b", "dvw5s", "nomem3s"):
        if arm not in A:
            continue
        for r in A[arm]["steps"]:
            w(f"| {arm} | {r['step']} | {r['n_cand']} | {r['n_kept']} | {f(r['M_all'])} | "
              f"{f(r['M_kept'])} | {r['mean_gcov_tok']:.3g} | "
              f"{100*r['share_groups_negcov']:.0f} | {f(r['len_neg_steps'],1)} | "
              f"{f(r['len_pos_steps'],1)} |")

    w("\n## B. Advantage-weighted token mass by action class (kept trajectories, pooled)\n")
    w("| arm | class | tokens | %tok | Σ A·tok | M_class [95% CI] |")
    w("|---|---|---|---|---|---|")
    for arm in arms:
        if arm not in B:
            continue
        for cl in CLASSES + ("scaffold",):
            d = B[arm][cl]
            w(f"| {arm} | {cl} | {d['tok']} | "
              f"{100*d.get('share_tok', float('nan')):.1f} | {d['S']:.3e} | "
              f"{f(d['M'])} [{f(d['lo'])}, {f(d['hi'])}] |")

    w("\n## D. dvw6b / prov6b format collapse\n")
    for arm in ("dvw6b", "prov6b"):
        w(f"\n**{arm}**\n")
        w("| step | n | used for an update | quick3 | RepeatedFormatError | steps_mean "
          "| resolve | exits |")
        w("|---|---|---|---|---|---|---|---|")
        for r in D[arm]["by_step"]:
            w(f"| {r['step']} | {r['n']} | {'yes' if r['in_tsv'] else 'IN FLIGHT'} | "
              f"{r['quick3']:.3f} | {r['rfe']:.3f} | "
              f"{r['steps_mean']:.1f} | {r['resolve']:.3f} | "
              f"{', '.join(f'{k}={v}' for k, v in sorted(r['exits'].items()))} |")
        w("")
        w("Per-slot-kind RepeatedFormatError share (bare / real note / foil):")
        w("")
        w("| step | " + " | ".join(sorted({k for r in D[arm]["by_step"]
                                           for k in r["by_kind_rfe"]})) + " |")
        ks = sorted({k for r in D[arm]["by_step"] for k in r["by_kind_rfe"]})
        w("|---" * (len(ks) + 1) + "|")
        for r in D[arm]["by_step"]:
            cells = []
            for k in ks:
                v = r["by_kind_rfe"].get(k)
                cells.append("n/a" if not v or v[0] is None else f"{v[0]:.2f} (n={v[1]})")
            w(f"| {r['step']} | " + " | ".join(cells) + " |")
    mal = D["dvw6b"].get("malformed") or {}
    if mal:
        w("\n### What the malformed turns are\n")
        w("mini-swe-agent discards the offending assistant turn -- on a format failure it "
          "appends only a `Format error:` user message and re-queries -- so the malformed "
          "text is NOT in the stored trajectory and cannot be shown verbatim. What is "
          "stored is the harness's own complaint, which names how many action blocks it "
          "found.\n")
        w("| step | RFE rollouts | `found N` complaints | mean assistant-turn chars "
          "| p90 chars | turns with no fence | api_calls of the RFE rollouts |")
        w("|---|---|---|---|---|---|---|")
        for st, r in sorted(mal.items()):
            w(f"| {st} | {r['n_rfe']} | "
              f"{', '.join(f'found {k}: {v}' for k, v in sorted(r['found'].items())) or '-'} | "
              f"{f(r.get('mean_asst_chars'), 0)} | {r.get('p90_asst_chars')} | "
              f"{f(r.get('share_turns_no_fence'), 4)} | "
              f"{', '.join(f'{k}:{v}' for k, v in sorted(r['api_calls'].items(), key=lambda x: (x[0] is None, x[0]))) or '-'} |")
        for st, r in sorted(mal.items()):
            for ex in r["examples"][:3] if st == max(mal) else []:
                w(f"\nLast well-formed turn before the silence "
                  f"(`{ex['base']}`, {ex['n_asst']} turns kept, "
                  f"n_steps_raw={ex['n_steps_raw']}):\n")
                w("```")
                w(ex["last_wellformed"])
                w("```")

    w("\n## E. Counterfactual update weightings (no training)\n")
    w("Generic-token coefficient = Σ_i w_i · (agent steps of i); a class token that occurs "
      "at constant rate per step picks up `rate x` this number.\n")
    w("| arm | scheme | generic coeff | view | test | edit | scaffold | Kish n_eff | steps with no A>0 |")
    w("|---|---|---|---|---|---|---|---|---|")
    for arm in arms:
        if arm not in E:
            continue
        for nm in ("tok_sum", "traj_mean", "pos_tokmean", "pos_toksum"):
            s = E[arm]["schemes"][nm]
            w(f"| {arm} | {nm} | {s['_generic']:.4f} | {s['view']:.4f} | {s['test']:.4f} | "
              f"{s['edit']:.4f} | {s['_scaffold']:.4f} | {s['_kish_mean']:.1f} | "
              f"{E[arm]['n_zero_pos_steps']}/{E[arm]['n_steps']} |")
    w("")
    w("The four schemes carry different normalisers (`tok_sum` divides by the step's total "
      "assistant tokens, the mean schemes by the trajectory's own length times the number "
      "of kept/positive trajectories), so compare signs and class structure, not "
      "magnitudes across rows. The algebra behind them is the next table.")
    w("")
    w("| arm | Σ A (all present) | Σ A (candidates) | Σ A (KEPT) | Σ|A| (kept) | "
      "Σ A·n (kept) | Σ A⁺·n (kept) |")
    w("|---|---|---|---|---|---|---|")
    for arm in arms:
        if arm not in E:
            continue
        d = E[arm]
        w(f"| {arm} | {d['sumA_present']:+.3f} | {d['sumA_cand']:+.3f} | "
          f"{d['sumA_kept']:+.3f} | {d['sumAbs_kept']:.1f} | {d['sumAn_kept']:+.3e} | "
          f"{d['sumAn_pos_kept']:+.3e} |")
    w("")
    w("`Σ A (all present)` is the exact-zero the group centring guarantees (it is exactly "
      "0 for `--adv-center group`; the `bare` / `subgroup` centrings of generations 5-6 "
      "do not centre on the whole group, so theirs is not 0). `Σ A (KEPT)` is what a "
      "trajectory-mean loss would actually see **after** the `--tok-budget` top-|A| pick, "
      "and it is NOT zero -- the pick is the second source of asymmetry, on top of the "
      "length weighting.")

    w("\n## C. Test-frame behaviour\n")
    if C.get("arms"):
        w("| exam arm | n | reps | resolve | steps | inv steps before 1st edit | "
          "no test after last edit | no test at ALL | %Submitted | %format | "
          "%step-limit | %ctx-limit |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for arm, d in sorted(C["arms"].items()):
            w(f"| {arm} | {d['n']} | {d['n_reps']} | {f(d['resolve'])} | "
              f"{f(d['steps_mean'],1)} | {f(d['inv_steps'],2)} | "
              f"{f(d['untested_submit'])} | {f(d.get('no_test_at_all'))} | "
              f"{f(d.get('share_submitted'))} | {f(d['share_format_exit'])} | "
              f"{f(d.get('share_limit_exit'))} | {f(d.get('share_ctx_exit'))} |")
        dose = C.get("dose") or {}
        w("")
        w(f"Dose (cumulative negative kept mass) rank correlations over "
          f"{dose.get('n')} checkpoints: investigation steps rho="
          f"{f(dose.get('rho_inv'))}, no-test-after-last-edit rho="
          f"{f(dose.get('rho_untested'))}, no-test-at-all rho="
          f"{f(dose.get('rho_notest'))}, format-exit rho={f(dose.get('rho_format'))}, "
          f"resolve rho={f(dose.get('rho_resolve'))}. Ordering by cumulative M instead: "
          f"investigation rho={f(dose.get('rho_inv_cumM'))}, no-test-at-all rho="
          f"{f(dose.get('rho_notest_cumM'))}. Ordering by cumulative KL from the lane tsv: "
          f"investigation rho={f(dose.get('rho_inv_cumKL'))}, no-test-at-all rho="
          f"{f(dose.get('rho_notest_cumKL'))}. All three doses are increasing in the "
          f"checkpoint index within an arm, so these pool 'more dose' with 'more "
          f"training'; the cross-arm correlation at a FIXED checkpoint separates them:")
        w("")
        w("| checkpoint | arms | rho(dose, inv steps) | rho(dose, no test at all) "
          "| rho(dose, resolve) |")
        w("|---|---|---|---|---|")
        for k, v in sorted((dose.get("by_ckpt") or {}).items()):
            w(f"| {k} | {v['n_arms']} | {f(v['rho_inv'])} | {f(v['rho_notest'])} | "
              f"{f(v['rho_resolve'])} |")
        w("")
        w("| exam arm | train arm | ckpt | cum negative mass | cum M | cum KL | inv steps "
          "| no test at all | format exits | resolve |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for x in sorted(dose.get("points") or [], key=lambda z: (z["train_arm"], z["ckpt"])):
            w(f"| {x['arm']} | {x['train_arm']} | {x['ckpt']} | {x['cum_neg_mass']:.3e} | "
              f"{f(x['cum_M'],2)} | {f(x.get('cum_KL'),4)} | {f(x['inv'],2)} | "
              f"{f(x.get('no_test'))} | {f(x['fmt'])} | {f(x['resolve'])} |")
        w("")
        w("### Paired differences from the comparator (common cells, chain-cluster CI)\n")
        w("| exam arm | comparator | cells | d inv steps [CI] | d no-test-after-edit "
          "[CI] | d no-test-at-all [CI] | d resolve [CI] | d steps [CI] |")
        w("|---|---|---|---|---|---|---|---|")
        for arm, row in sorted((C.get("paired") or {}).items()):
            def cell(x):
                if not x:
                    return "n/a"
                return f"{x['delta']:+.2f} [{x['lo']:+.2f}, {x['hi']:+.2f}]"
            n = (row.get("inv") or {}).get("n_cells", 0)
            w(f"| {arm} | {row['comparator']} | {n} | {cell(row.get('inv'))} | "
              f"{cell(row.get('untested'))} | {cell(row.get('notest'))} | "
              f"{cell(row.get('resolve'))} | {cell(row.get('steps'))} |")
    else:
        w("_exam cache missing_")

    w("\n## Data-quality caveats\n")
    w("""
* **Contamination.** Everything carrying the `.dmg0920` suffix of the 2026-09-20 claim-lock
  incident (ledger §8.93) lives in `actor27_rollout_results_dmg0920/` and is never read.
  Steps are further restricted to those with a row in the arm's lane tsv, which drops the
  step whose rollouts are being collected right now and the first-attempt leftovers that
  were not renamed (`dvw6b` s12 1 file, s13 6 files; `prov6b` s8 2, s9 8). `dvw6b` keeps
  updates 0-7 plus the redone step 8; `prov6b` keeps 0-4 plus the redone step 5, which the
  ledger's addendum 3 shows was a 4-dead-slot zero-gradient step (`n_used = 0`), so it
  contributes nothing.
* **Reconstruction.** `n_used` reproduces the tsv exactly on 144/205 (arm, step) pairs and
  on 14/14 for dvw3s, prov3s, nomem3s, dvw5s, prov5s. The mismatches are steps where a
  straggler was imputed at harvest and its worker wrote the result afterwards: the rollout
  set is the same, the top-|A| pick is not. `M_all` does not depend on the pick.
* **Provenance gating is not applied.** For the `prov*` arms the trainer multiplies the
  policy term by a per-token weight `w_tok = (1-kappa) + kappa*omega_t` (kappa = 1,
  `--route-nonprov` on), whose per-rollout mean in `actor27_prov*_omega.jsonl` is small on
  note-carrying rollouts. The masses reported here are ungated `A·n_tok`, so the prov arms'
  realised magnitudes are smaller than shown; `w_tok >= 0` so no sign changes.
* **Coverage.** `dvw` has tsv rows only for steps 16-25 (the earlier rows are in a
  `.blowup` backup), so it contributes 10 of its 26 steps. `dvw6g` and `nomem6b` have 2
  steps each, `prov6b` 6, `dvw6b` 9 -- their CIs are wide and their pooled numbers should
  not be read as arm-level facts.
* **Exam completeness.** `mem_v5knote_a27nomem4ss14_self` has 2 replays (93 sessions),
  `mem_v5knote_a27prov5ss6_self` 1 replay, `a27prov6bs4_self` and `a27nomem4ss12_self` are
  partial; everything else is 3 x 146. The comparator is `ft1_grpo/mem_v5knote_w27b`
  (98K, self-written notes); `ft1_actor9/mem_v5knote_a27base` is a 64K 4B-writer control
  and is reported but never paired with the 98K arms.
* **The malformed turns cannot be shown verbatim.** mini-swe-agent drops the offending
  assistant message and appends only its own `Format error:` complaint, so the text that
  failed to parse is not on disk anywhere.
* **The `other` class is load-bearing.** It is 12-22 % of the kept tokens and carries
  strongly negative mass in 15/16 arms. Spot checks show it is dominated by `python -c`
  reproduction/verification runs, which are neither a file read nor a test-suite
  invocation. Some of the "verify less" pressure therefore sits in `other`, not in `test`.
* **Class labels are per message, by precedence** submit > edit > test > view > other, so
  the five class masses sum to the trajectory total. A multi-label variant (a turn counts
  for every class it touches) is in the json under `B.<arm>.<class>.multi`.
* **Scaffold tokens are an estimate**: the tokenised length of the fence open/close lines,
  the `THOUGHT:` marker and the closing `<|im_end|>`, capped at the span length. It is a
  lower bound on the fixed-format tokens.
""".strip())
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
