#!/usr/bin/env python3
"""Table 3 (ledger cost table): 27B untrained arms, one ledger batch, full sample = test half + training half.

    python3 analysis/ledger_table/make_table3_ledger.py [--out DIR]
        # writes table3_ledger_full.tex and table3_ledger_full.md to DIR (default analysis/ledger_table/out/)

2026-09-29 (ledger 8.118). New file; make_main_avg.py and main*.tex are not touched.

Sources. Every row reads two run-ids of the same lane recipe (98K window, native coder-next serving, RSM_LEDGER=1,
step limit 100, grade timeout 5400): ft1_ledger over the 48 test chains (frame_test_v1_chains.txt) and tr1_ledger
over the 51 training chains (split_v2_train.txt; the one registered exclusion of amendment A5 is not run).
Arm directories carry the same names in both run-ids, so a row is one arm name read from both.

Aggregation (differs from Table 2 on purpose, see the caption).
  1. A replay counts in a half only at full coverage of that half (every slot at pos >= 2 has a results row and a
     ledger summary). Per-half completeness is printed on the console.
  2. Per slot, the mean over every complete replay of that half ("per-slot mean first").
  3. Common slots = intersection over all rows; each cell is the mean over the common slots of the stratum.
  4. The +- subscript is the across-replay SD of whole-frame replays (a replay suffix present and complete in every
     half in use), each reduced to its own mean over the common slots; no subscript with fewer than two.
  5. Chain columns (USD, writer side) sum every ledger session of a chain (pos 1, 2, 3) and average over the
     chains whose pos 2 and pos 3 slots are common and whose pos 1 session has a ledger summary in every row.
Until every row has at least one complete tr1_ledger replay, the table renders the test half only and the caption
says the training half is in flight. Rerunning after tr1_ledger lands fills the full sample with no edits.

Ledger fields are read by train-corpus/ledger_rollup.py's own load_arm (calls.jsonl per slot, summarised by
chainswe/harness/ledger.py summarize); no ledger logic is copied here. Resolve, steps and the linked flag come
from exam_table3.load, as in Table 2. Visual conventions (\\sdsub, \\mmc, change tags, groups) are Table 2's,
imported from make_main_avg.py.
"""
import sys, pathlib, statistics as st, collections
import os

# Path handling (analysis/ledger_table copy). Sibling modules are imported from this directory. The repo root is
# two levels above this file unless RSM_ROOT is set, run directories are <root>/runs unless RSM_RUNS is set (both
# resolved in exam_table3.py), and chain lists are read from <root>/data/chains/. Outputs go to --out DIR, else
# RSM_TABLE_OUT, else analysis/ledger_table/out/.
HERE = pathlib.Path(__file__).resolve().parent
_saved = list(sys.argv)
OUT = pathlib.Path(os.environ.get("RSM_TABLE_OUT") or (HERE / "out"))
if "--out" in _saved:
    _i = _saved.index("--out"); OUT = pathlib.Path(_saved[_i + 1]); del _saved[_i:_i + 2]
sys.argv = [sys.argv[0]]                       # ledger_rollup parses its own argv at import
sys.path.insert(0, str(HERE))
import ledger_rollup as LR                     # noqa: E402  (imports exam_table3 as LR.T3)
import neg_mass_hypothesis as NM               # noqa: E402  (project cluster bootstrap: NM.boot_ci)
import make_main_avg as MMA                    # noqa: E402  (Table 2 helpers: num, delta_tag, labels, disc)
sys.argv = _saved
T3, L = LR.T3, LR.L
R = T3.R

import os
# 2026-09-29 spec revision (Peiwen): Table 3 = TEST split (96 slots). The full-sample path (train half from
# tr1_ledger) stays in the code behind TABLE3_FULL=1 for a possible later full-sample table.
HALVES = [("test", "ft1_ledger", "frame_test_v1_chains.txt")] + \
    ([("train", "tr1_ledger", "split_v2_train.txt")] if os.environ.get("TABLE3_FULL") == "1" else [])
ROWS = [("seq", "seq_w27b"), ("rawcat", "mem_rawcat_w27b"), ("factsall", "mem_factsall_w27b"),
        ("memop", "mem_memop_w27b"), ("rbankj", "mem_rbankj_w27b"),
        ("v5w4b", "mem_v5knote_base4b_w27b"), ("v5self", "mem_v5knote_w27b"), ("v5inline", "mem_v5inline_w27b"),
        # trained-actor block (2026-09-29 spec revision): LoRA serving path, adapter + coder-next registered on one
        # server, --served-name trained; the ledger lanes of 8.118 write these arms into ft1_ledger.
        ("t0seq", "seq_a27dvw6bs0_led"), ("t0note", "mem_v5knote_a27dvw6bs0_selfled"),
        ("t7seq", "seq_a27dvw7as20_led"), ("t7note", "mem_v5knote_a27dvw7as20_selfled"),
        ("t8seq", "seq_a27prov8ds20_led"), ("t8note", "mem_v5knote_a27prov8ds20_selfled"),
        ("t8inline", "mem_v5inline_a27prov8ds20")]
# 2026-09-29 Peiwen: keep only the in-session variant visible in the Ours group; the (4B) and
# (self, separate-request) rows stay in the .tex as "% [hidden]" comments.
HIDE = {"v5w4b", "v5self"}
GROUPS = [("Ref.", ["seq", "rawcat"]),
          (r"\shortstack[l]{Open-source\\memory}", ["factsall", "memop", "rbankj"]),
          (r"\textbf{Ours}", ["v5w4b", "v5self", "v5inline"]),
          (r"\shortstack[l]{\textbf{Ours}\\(trained actor)}", ["t0seq", "t0note", "t7seq", "t7note", "t8seq", "t8note", "t8inline"])]
LAB = dict(MMA.RLAB,
           # 2026-09-30 Peiwen: no parenthetical remarks in the memory-layer column (table width)
           seq="No memory", seq2="No memory", rawcat="Raw concat", factsall=r"Mem0$^\dagger$", memop=r"MemOp$^\S$",
           rbankj=r"ReasoningBank$^\P$", v5w4b=r"\textbf{Fused-v5, 4B writer}", v5self=r"\textbf{Fused-v5, separate writer call}",
           v5inline=r"\textbf{Fused-v5}",
           t0seq=r"No memory, untrained control", t0note=r"Fused-v5, separate writer call, untrained control",
           t7seq=r"No memory, actor dvw7a s20", t7note=r"Fused-v5, separate writer call, actor dvw7a s20",
           t8seq=r"No memory, actor prov8d s20", t8note=r"\textbf{Fused-v5, separate writer call, actor prov8d s20}",
           t8inline=r"Fused-v5, actor prov8d s20")
PLAIN = dict(MMA.PLAIN, rbankj="ReasoningBank (judge labels)",
             t0seq="no memory, actor untrained (same config)", t0note="Fused v5 (self), actor untrained (same config)",
             t7seq="no memory, actor dvw7a s20", t7note="Fused v5 (self), actor dvw7a s20",
             t8seq="no memory, actor prov8d s20", t8note="Fused v5 (self), actor prov8d s20",
             t8inline="Fused v5 (self, in-session), actor prov8d s20")
NO_TAG = {"seq"}


def chains(fn):
    return {l.strip() for l in open(R / f"data/chains/{fn}") if l.strip()}


def load_half(run, arm, chs):
    """one replay of one half -> ({slot: T3 row}, {(chain, pos): ledger summary incl. pos 1})"""
    T3.TEST = chs
    t3 = T3.load(run, arm)
    LR.ORDERS = (1, 2, 3)                     # chain sums need the pos-1 session too; load_arm reads this global
    led = LR.load_arm(run, arm)
    return t3, led


def gather():
    info = {}
    for key, arm in ROWS:
        info[key] = {}
        for half, run, fn in HALVES:
            chs = chains(fn)
            denom = 2 * len(chs)
            reps = {}
            for suf in MMA.disc(run, arm):
                t3, led = load_half(run, arm + suf, chs)
                slots = [k for k in t3 if k in led]
                reps[suf] = dict(t3=t3, led=led, cov=len(slots), complete=len(slots) >= denom,
                                 noledger=sum(1 for v in led.values() if v["src"] != "calls.jsonl"),
                                 est=[v["cached_src_estimated_share"] for v in led.values()
                                      if v.get("cached_src_estimated_share") is not None])
            info[key][half] = dict(run=run, denom=denom, reps=reps)
    return info


def analyse(info):
    full = len(HALVES) > 1 and all(any(r["complete"] for r in info[k]["train"]["reps"].values()) for k, _ in ROWS)
    halves = ["test", "train"] if full else ["test"]
    # per-slot mean over the complete replays of each half
    per = {}
    for key, _ in ROWS:
        acc = collections.defaultdict(list)
        cacc = collections.defaultdict(list)
        for h in halves:
            for suf, r in info[key][h]["reps"].items():
                if not r["complete"]:
                    continue
                for k, v in r["t3"].items():
                    if k in r["led"]:
                        acc[k].append((v, r["led"][k]))
                for c in {k[0] for k in r["t3"]}:
                    ss = [r["led"].get((c, p)) for p in (1, 2, 3)]
                    if all(ss):
                        cacc[c].append(ss)
        per[key] = (acc, cacc)
    active = [k for k, _ in ROWS if per[k][0]]          # a row with no complete replay yet is left out
    keys = sorted(set.intersection(*[set(per[k][0]) for k in active])) if active else []
    chainset = sorted(c for c in set.intersection(*[set(per[k][1]) for k in active])
                      if (c, 2) in keys and (c, 3) in keys) if keys else []

    def slot_vals(v, led):
        # 2026-09-29 Peiwen: token reuse, per session, all-in (actor + writer + memother):
        # reuse = 1 - sum(uncached_prompt) / sum(prompt), in percent.
        pr = led["tok_actor_prompt"] + led["tok_writer_prompt"] + led["tok_memother_prompt"]
        un = led["tok_actor_uncached"] + led["tok_writer_uncached"] + led["tok_memother_uncached"]
        return dict(res=100.0 * v["res"], steps=v["steps"], ca=led["tok_cacheaware_total"] / 1000,
                    raw=led["tok_raw_total"] / 1000, reuse=(100.0 * (1 - un / pr) if pr else 0.0),
                    linked=v["linked"])

    def chain_vals(ss):
        side = sum(s["tok_writer_uncached"] + s["tok_writer_completion"] + s["tok_memother_uncached"]
                   + s["tok_memother_completion"] for s in ss) / 1000
        return dict(usd=sum(s["cost_usd_priced"] for s in ss), side=side)

    def cellset(slotmap, chainmap):
        """slotmap: slot -> dict of metrics (already averaged); -> {(stratum, metric): (mean, median)}"""
        out = {}
        for sn, sel in (("L", lambda d: d["linked"]), ("U", lambda d: not d["linked"]), ("O", lambda d: True)):
            ks = [k for k in keys if sel(slotmap[k])]
            for m in ("res", "steps", "ca", "raw", "reuse"):
                vs = [slotmap[k][m] for k in ks]
                out[(sn, m)] = (st.mean(vs), st.median(vs)) if vs else (None, None)
        for m in ("usd", "side"):
            vs = [chainmap[c][m] for c in chainset]
            out[("O", m)] = (st.mean(vs), st.median(vs)) if vs else (None, None)
        # ratio cells, formed inside one frame summary (make_main_avg stratstats convention); for the pooled
        # summary they are overwritten below by the mean of the per-replay ratios
        res, ca, usd = out[("O", "res")][0], out[("O", "ca")][0], out[("O", "usd")][0]
        out[("O", "eff")] = ((res / ca) if res is not None and ca else None, None)
        out[("O", "resusd")] = ((res / usd) if res is not None and usd else None, None)
        return out

    rows = {}
    for key, arm in ROWS:
        acc, cacc = per[key]
        if not keys:
            break
        if key not in active:
            continue
        linked = {k: acc[k][0][0]["linked"] for k in keys}
        sm = {}
        for k in keys:
            vs = [slot_vals(v, led) for v, led in acc[k]]
            sm[k] = {m: st.mean(x[m] for x in vs) for m in ("res", "steps", "ca", "raw", "reuse")}
            sm[k]["linked"] = linked[k]
        cm = {c: {m: st.mean(chain_vals(ss)[m] for ss in cacc[c]) for m in ("usd", "side")} for c in chainset}
        main = cellset(sm, cm)
        # whole-frame replays for the SD subscript
        sufs = set.intersection(*[{s for s, r in info[key][h]["reps"].items() if r["complete"]} for h in halves])
        reps = []
        for suf in sorted(sufs, key=lambda s: (len(s), s)):
            smr, cmr = {}, {}
            for h in halves:
                r = info[key][h]["reps"][suf]
                for k in keys:
                    if k in r["t3"] and k in r["led"]:
                        smr[k] = slot_vals(r["t3"][k], r["led"][k])
                for c in chainset:
                    ss = [r["led"].get((c, p)) for p in (1, 2, 3)]
                    if all(ss):
                        cmr[c] = chain_vals(ss)
            if len(smr) == len(keys) and len(cmr) == len(chainset):
                reps.append(cellset(smr, cmr))
        for m in ("eff", "resusd"):                       # mean of per-replay ratios, never a ratio of means
            vs = [r[("O", m)][0] for r in reps if r[("O", m)][0] is not None]
            main[("O", m)] = (st.mean(vs) if vs else None, None)
        sd = {cm_: (st.stdev([r[cm_][0] for r in reps]) if len(reps) >= 2 else None) for cm_ in main}
        n_half = {h: sum(1 for r in info[key][h]["reps"].values() if r["complete"]) for h in info[key]}
        # note-turn prefix-cache hit of the in-session variant (ledger_rollup's inline_hit), mean over the sessions
        # of every complete replay on the common slots; None for arms without an in-session note turn
        hits = [led["inline_hit"] for k in keys for _, led in acc[k] if led.get("inline_hit") is not None]
        est = [led["cached_src_estimated_share"] for k in keys for _, led in acc[k]
               if led.get("cached_src_estimated_share") is not None]
        rows[key] = dict(sm=sm, cells={cm_: (main[cm_][0], sd[cm_], main[cm_][1]) for cm_ in main},
                         n_half=n_half, n_whole=len(reps), inline_hit=(st.mean(hits) if hits else None),
                         n_hit=len(hits), est_share=(st.mean(est) if est else None))
    # 2026-09-29 Peiwen: paired chain-cluster bootstrap vs the no-memory row on Overall Res%, Steps, Res/Tok
    if "seq" in rows:
        for key in rows:
            if key == "seq":
                continue
            rows[key]["ci"] = {m: paired_ci(rows[key]["sm"], rows["seq"]["sm"], keys, m) for m in ("res", "steps", "eff")}
    nL = sum(1 for k in keys if per[active[0]][0][k][0][0]["linked"]) if keys else 0
    return dict(full=full, halves=halves, nslots=len(keys), nL=nL, nU=len(keys) - nL,
                nchains=len(chainset), rows=rows)


BODY = [(s, m) for s in ("L", "U") for m in ("res", "steps", "ca")] + \
    [("O", m) for m in ("res", "steps", "ca", "raw", "eff", "usd", "side")]
# .md twin only: auxiliary readings (Res per USD-chain, prefix-cache reuse %); never in the .tex layout
MD_BODY = BODY + [("O", "resusd"), ("O", "reuse")]
DIG = {"res": 1, "steps": 1, "ca": 1, "raw": 1, "eff": 2, "resusd": 1, "reuse": 1, "usd": 2, "side": 1}
MED = {"steps", "ca", "raw"}
TAGM = {"res": "res", "steps": "steps", "ca": "tok", "raw": "tok", "eff": "eff", "resusd": "eff", "reuse": "res",
        "usd": "tok", "side": None}

HEAD = r"""\begin{table*}[!t]
\centering
\small
\setlength{\tabcolsep}{3pt}
\caption{CAPTION}
\label{tab:ledger-full}
\resizebox{\linewidth}{!}{
\begin{tabular}{l|l|CCC|CCC|CCCCCCM}
\toprule
\multicolumn{1}{c|}{\multirow{2}{*}{\textbf{Type}}} & \multicolumn{1}{c|}{\multirow{2}{*}{\textbf{Memory layer}}}
& \multicolumn{3}{c|}{\textbf{Linked} (dependency present)}
& \multicolumn{3}{c|}{\textbf{Unlinked} (independent)}
& \multicolumn{7}{c}{\textbf{Overall}} \\
\cmidrule(lr){3-5}\cmidrule(lr){6-8}\cmidrule(lr){9-15}
\multicolumn{1}{c|}{} & \multicolumn{1}{c|}{}
& {\scriptsize\shortstack{\textbf{Resolve}\\\textbf{(\%)} $\uparrow$}} & {\scriptsize\shortstack{\textbf{Steps}\\\textbf{mean (med.)} $\downarrow$}} & {\scriptsize\shortstack{\textbf{Cache-aware}\\\textbf{K / session} $\downarrow$}}
& {\scriptsize\shortstack{\textbf{Resolve}\\\textbf{(\%)} $\uparrow$}} & {\scriptsize\shortstack{\textbf{Steps}\\\textbf{mean (med.)} $\downarrow$}} & {\scriptsize\shortstack{\textbf{Cache-aware}\\\textbf{K / session} $\downarrow$}}
& {\scriptsize\shortstack{\textbf{Resolve}\\\textbf{(\%)} $\uparrow$}} & {\scriptsize\shortstack{\textbf{Steps}\\\textbf{mean (med.)} $\downarrow$}} & {\scriptsize\shortstack{\textbf{Cache-aware}\\\textbf{K / session} $\downarrow$}} & {\scriptsize\shortstack{\textbf{Raw}\\\textbf{K / session} $\downarrow$}} & {\scriptsize\shortstack{\textbf{Res/Tok}\\\textbf{(pp per K)} $\uparrow$}} & {\scriptsize\shortstack{\textbf{USD}\\\textbf{/ chain} $\downarrow$}} & {\scriptsize\shortstack{\textbf{Writer side}\\\textbf{K / chain}}} \\"""


BOOT_REPS, BOOT_SEED = 2000, 0


def paired_ci(sa, sb, keys, m):
    """Paired chain-cluster bootstrap of (arm - no-memory) on the common slots, per-slot means over replays first.
    Resampling is NM.boot_ci's own (chains as clusters, 2000 resamples, seed 0); its stat hook is used to collect the
    bootstrap distribution, so the 99% interval comes from the very same resamples as its 95% interval.
    res / steps: mean paired difference. eff: difference of the two ratios mean(res)/mean(cache-aware K) on the
    resampled slots (the table's eff cell is a mean of per-replay ratios; the test is on the per-slot-mean ratio)."""
    vals = [(sa[k], sb[k]) for k in keys]
    clus = [k[0] for k in keys]
    if m == "eff":
        def f(xs):
            a = sum(x[0]["res"] for x in xs) / max(1e-12, sum(x[0]["ca"] for x in xs))
            b = sum(x[1]["res"] for x in xs) / max(1e-12, sum(x[1]["ca"] for x in xs))
            return a - b
    else:
        def f(xs):
            return sum(x[0][m] - x[1][m] for x in xs) / len(xs)
    seen = []
    def stat(xs):
        v = f(xs); seen.append(v); return v
    point, lo95, hi95 = NM.boot_ci(vals, clus, reps=BOOT_REPS, seed=BOOT_SEED, stat=stat)
    dist = sorted(seen[1:])                       # first call is the point estimate
    lo99, hi99 = dist[int(0.005 * len(dist))], dist[min(len(dist) - 1, int(0.995 * len(dist)))]
    stars = "**" if (lo99 > 0 or hi99 < 0) else "*" if (lo95 > 0 or hi95 < 0) else ""
    return dict(d=point, lo95=lo95, hi95=hi95, lo99=lo99, hi99=hi99, stars=stars, n=len(set(clus)))


def fmt(cell, base, m, key, star=""):
    mean, sd, med = cell
    if mean is None:
        return "--"
    tag = "" if (key in NO_TAG or base is None or TAGM[m] is None) else MMA.delta_tag(mean, base, TAGM[m])
    if star and tag:
        tag = tag.replace(r"\%}", r"\%" + star + "}", 1)
    if m == "side" and key in ("seq", "rawcat"):
        return "0"
    return MMA.num(mean, sd, d=DIG[m], tag=tag, med=(med if m in MED else None))


def build(A, missing):
    s = A
    frame = (r"the full sample, the $48$ test chains (run-id \texttt{ft1\_ledger}) and the $51$ training chains "
             r"(run-id \texttt{tr1\_ledger}) of the main pool" if s["full"] else
             r"the held-out test split, the $48$ test chains of \texttt{split\_v2\_test} (run-id \texttt{ft1\_ledger})")
    cap = (r"\textbf{What a memory layer costs, measured by the token ledger (Qwen3.8-27B-FP8, untrained actor).} "
           r"Frame is " + frame + rf"; ${s['nslots']}$ common slots at pos $\geq 2$ (${s['nL']}$ linked / "
           rf"${s['nU']}$ unlinked) and ${s['nchains']}$ common chains for the per-chain columns. "
           r"Each slot is first averaged over every complete replay of its arm, then cells average over the common "
           r"slots; the subscript is the across-replay SD of whole-frame replays. Change tags read against the "
           r"no-memory row of the same block, as a percentage of that row's value; "
           r"\dg{blue} marks an improvement and \dl{light blue} a deterioration.")  # 2026-09-30: Table 2/3 hidden in main.tex, no cross-ref
    out = [HEAD.replace("CAPTION", cap), r"\midrule"]
    first = True
    base = A["rows"].get("seq")
    for lead, ks in GROUPS:
        ks = [k for k in ks if k in A["rows"]]
        if not ks:
            continue
        if not first:
            out.append(r"\cmidrule{1-15}")
        first = False
        vis = [k for k in ks if k not in HIDE]
        j = 0
        for k in ks:
            r = A["rows"][k]
            cells = " & ".join(fmt(r["cells"][(sn, m)], base["cells"][(sn, m)][0] if base else None, m, k,
                                   star=(r.get("ci", {}).get(m, {}).get("stars", "") if sn == "O" else ""))
                               for sn, m in BODY)
            lab = LAB[k] + rf" {{\scriptsize (average@{r['n_whole']})}}"
            if k in HIDE:
                out.append(f"% [hidden] & {lab} & {cells} \\\\")
                continue
            head = rf"\multirow{{{len(vis)}}}{{*}}{{{lead}}}" if j == 0 else ""
            j += 1
            out.append(f"{head} & {lab} & {cells} \\\\")
    nrows = ", ".join(f"{PLAIN[k]} $n{{=}}{A['rows'][k]['n_half']['test']}"
                      + (f"{{+}}{A['rows'][k]['n_half']['train']}" if A["full"] else "") + "$"
                      for k, _ in ROWS if k in A["rows"])
    fn = (r"\textbf{Replays per row} (complete replays per half" + (r", test${+}$training" if A["full"] else "") +
          r"). " + nrows + r". \textbf{Ledger columns.} Per-session figures are the ledger of the successor "
          r"session at pos $\geq 2$ over every call it made, actor and memory side alike. \emph{Cache-aware} counts "
          r"uncached prompt plus completion tokens as returned by the serving engine (prefix-cache hits are free), "
          r"\emph{Raw} counts prompt plus completion. \emph{USD / chain} sums every session of a chain (pos $1$ to "
          r"$3$, note writing included) at one set of API list rates for all rows (\texttt{prices.json}, row "
          + L.price_row()["_row"] + r"), a deployment-free conversion and not an expense. \emph{Writer side} is the "
          r"cache-aware tokens of note writing, extraction, retrieval and judging per chain; it is $0$ for the "
          r"no-memory and raw-concat rows, which call no model outside the actor. \textbf{Rows.} $^\dagger$Mem0 extraction-union and $^\S$MemOp guidance document, official "
          r"prompts on the local writer; $^\P$ReasoningBank with its official LLM-judge labels. (4B) is a separate "
          r"untrained Qwen3.5-4B writer, (self) the actor writing its own note at the boundary, (self, in-session) "
          r"the same note written as the last turn of the actor's own session. \textbf{Trained actor.} The untrained "
          r"rows serve the frozen $27$B model natively; the trained-actor rows serve it through a LoRA adapter "
          r"(prefix caching on, $98$K window, note written by the base model on the same server), and the "
          r"zero-update checkpoint of that pipeline (untrained, same config) is listed so the trained rows can be "
          r"read against a control that shares their serving path. Their change tags still read against the "
          r"block's no-memory row, which ran natively.")
    # 2026-09-29 Peiwen (correction): efficiency column = the old Table 1 Res./Tok convention on the ledger denominator.
    fn += (r" \textbf{Res/Tok} divides a replay's overall resolve (\%) by that same replay's overall cache-aware "
           r"K per session, i.e. resolved percentage points per thousand newly computed tokens, and higher is better; "
           r"the cell is the mean and SD of these per-replay ratios, never a ratio "
           r"of pooled means. (average@$n$) after a label is the number of replays that row averages. "
           r"$^{*}$\,/\,$^{**}$ on an Overall change tag of Resolve, Steps or Res/Tok: the paired chain-cluster "
           r"bootstrap $95\%$ / $99\%$ CI vs the no-memory row excludes zero, $2000$ resamples over the "
           rf"${A['nchains']}$ chains.")
    if missing:
        fn += " " + r"\textbf{Missing so far.} " + "; ".join(missing) + "."
    out += [r"\bottomrule", r"\end{tabular}}",
            r"\par\vspace{2pt}\begin{minipage}{\textwidth}\scriptsize " + fn + r"\end{minipage}",
            r"\end{table*}"]
    return "\n".join(out) + "\n"


def md(A):
    hdr = ["Memory layer"] + [f"{s} {m}" for s, m in MD_BODY]
    lines = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for _, ks in GROUPS:
        for k in ks:
            if k not in A["rows"]:
                continue
            r = A["rows"][k]
            def f(c, m):
                if c[0] is None:
                    return "--"
                s = f"{c[0]:.{DIG[m]}f}" + (f" ±{c[1]:.{DIG[m]}f}" if c[1] is not None else "")
                return s + (f" ({c[2]:.{DIG[m]}f})" if m in MED else "")
            lines.append("| " + " | ".join([PLAIN[k] + (" (hidden in .tex)" if k in HIDE else "")]
                                           + [f(r["cells"][(s, m)], m) for s, m in MD_BODY]) + " |")
    hl = [f"- note-turn prefix-cache hit, {PLAIN[k]}: {100 * A['rows'][k]['inline_hit']:.1f}% "
          f"(mean over {A['rows'][k]['n_hit']} sessions)" for k in ("v5inline", "t8inline")
          if k in A["rows"] and A["rows"][k]["inline_hit"] is not None]
    hl = ["- O eff = Res/Tok, overall resolve pp per cache-aware K per session (the .tex column); O resusd = overall "
          "resolve pp per USD per chain; both are the mean of per-replay ratios (make_main_avg convention).",
          "- O reuse = auxiliary, .md only: share of a session's prompt tokens (actor + writer + memory side) served from "
          "the prefix cache, 1 - sum(uncached prompt)/sum(prompt); every call carries the engine-reported cached count "
          "(estimated share 0.000 on every row, ReasoningBank writer/judge included)."] + hl
    ci = ["", "## Paired chain-cluster bootstrap vs no memory (Overall; per-slot means over replays; "
          f"{BOOT_REPS} resamples over chains, seed {BOOT_SEED})", "",
          "| Row | n rep | dRes pp [95%] [99%] | dSteps [95%] [99%] | dRes/Tok [95%] [99%] |", "|---|---|---|---|---|"]
    for _, ks in GROUPS:
        for k in ks:
            r = A["rows"].get(k)
            if not r or "ci" not in r:
                continue
            c = r["ci"]
            cell = lambda x, d: (f"{x['d']:+.{d}f}{x['stars']} [{x['lo95']:+.{d}f}, {x['hi95']:+.{d}f}] "
                                 f"[{x['lo99']:+.{d}f}, {x['hi99']:+.{d}f}]")
            ci.append(f"| {PLAIN[k]}{' (hidden in .tex)' if k in HIDE else ''} | {r['n_whole']} | {cell(c['res'], 1)} | "
                      f"{cell(c['steps'], 1)} | {cell(c['eff'], 3)} |")
    return ("\n".join(lines) + "\n" + ("\n" + "\n".join(hl) + "\n" if hl else "")
            + "\n".join(ci) + "\n")


if __name__ == "__main__":
    info = gather()
    missing = []
    print("per-half completeness (complete replays / found; coverage per replay; sessions without calls.jsonl):")
    for key, arm in ROWS:
        for h, run, fn in HALVES:
            d = info[key][h]
            reps = d["reps"]
            ok = [s or "_r1" for s, r in reps.items() if r["complete"]]
            print(f"  {PLAIN[key]:<34} {h:<5} {run:<10} {len(ok)}/{len(reps)} complete  "
                  + " ".join(f"{(s or '_r1')}:{r['cov']}/{d['denom']}" for s, r in reps.items())
                  + f"  noledger={sum(r['noledger'] for r in reps.values())}"
                  + (f"  est_share={st.mean(x for r in reps.values() for x in r['est']):.3f}"
                     if any(r['est'] for r in reps.values()) else ""))
            if not ok:
                missing.append(f"{PLAIN[key]} ({h} half, \\texttt{{{run.replace('_', chr(92) + '_')}}})")
    A = analyse(info)
    print(f"\nframe: {'FULL (test+train)' if A['full'] else 'TEST SPLIT'}; common slots {A['nslots']} "
          f"(L {A['nL']} / U {A['nU']}); common chains {A['nchains']}; price row {L.price_row()['_row']}")
    print(f"{'row':<30}" + "".join(f"{s+':'+m:>13}" for s, m in MD_BODY) + "  n(test,train) whole")
    for _, ks in GROUPS:
        for k in ks:
            if k not in A["rows"]:
                print(f"{PLAIN[k]:<30} --"); continue
            r = A["rows"][k]
            print(f"{PLAIN[k]:<30}" + "".join(
                f"{('--' if r['cells'][c][0] is None else format(r['cells'][c][0], '.' + str(DIG[c[1]]) + 'f') + ('' if r['cells'][c][1] is None else '±' + format(r['cells'][c][1], '.' + str(DIG[c[1]]) + 'f'))):>13}"
                for c in MD_BODY) + f"  ({r['n_half']['test']},{r['n_half'].get('train', 0)}) {r['n_whole']}")
    miss_tex = [m for m in missing if "train half" not in m] if not A["full"] else missing
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "table3_ledger_full.tex").write_text(build(A, miss_tex))
    (OUT / "table3_ledger_full.md").write_text(
        f"Table 3 (ledger), {'full sample' if A['full'] else 'test split (96 slots)'}; "
        f"{A['nslots']} common slots (L {A['nL']} / U {A['nU']}), {A['nchains']} chains\n\n" + md(A)
        + ("\nMissing: " + "; ".join(missing) + "\n" if missing else ""))
    print("\nwrote", OUT / "table3_ledger_full.tex", "and", OUT / "table3_ledger_full.md")
