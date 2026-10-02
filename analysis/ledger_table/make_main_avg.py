#!/usr/bin/env python3
"""Regenerate paper/tables/main_{test,all}_avg.tex from run artifacts.

Average@N main tables, two actor blocks (9B / 27B), two frames (test / all-sample).
Conventions are exam_table3.py's: order in {2,3}, steps = n_steps,
Tok(K) = trajectory chars/4000, Inj(K) = injected chars/4000, linked flag task_link_flags_v2,
slots = strict intersection over every replay of every kept body row in the block.
A cell with >=2 complete replays prints mean and across-replay sample SD; a single replay prints itself.

2026-09-16: added the untrained v5 rows and the actor-line no-memory reference row.
Rerun after new replays land:  python3 paper/tables/make_main_avg.py

2026-09-23 external-block convention (see paper/tables/blocks/README.md). The DeepSeek actor block is produced on
the bouchet cluster and delivered as files, never by editing the generated .tex:
  blocks/deepseek_{test,all}_block.tex     spliced verbatim after the 27B block, just before \bottomrule
  blocks/deepseek_{test,all}_footnote.tex  spliced verbatim into the footnote, before "\textbf{Provenance.}" (test)
                                           or before "\textbf{Dashed rows.}" (all)
A missing file leaves a visible "% DeepSeek block file missing: <path>" comment at that spot; the table still builds.
2026-09-23 Table 1 27B untrained rows read run-id ft1_ledger. Replays are discovered automatically (any _rN
suffix); a ft1_ledger replay counts only at full 96/96 test-frame coverage (STRICT_FULL_RUNS), so in-flight
replays are skipped and picked up on the next rerun without edits.
"""
import sys, re, json, pathlib, statistics as st
import os

# Path handling (analysis/ledger_table copy): sibling modules are imported from this directory; the repo root
# and runs directory come from exam_table3 (RSM_ROOT / RSM_RUNS); outputs go to RSM_TABLE_OUT or ./out.
HERE = pathlib.Path(__file__).resolve().parent
sys.argv = [sys.argv[0], "--frame", "test"]
sys.path.insert(0, str(HERE))
import exam_table3 as T3                                     # noqa: E402

R = T3.R
OUT = pathlib.Path(os.environ.get("RSM_TABLE_OUT") or (HERE / "out"))
FRAMES = {"test": "frame_test_v1_chains.txt", "all": "v2main100.txt"}
MIN_COV = 0.95
# 2026-09-17 user ruling: the 9B block will be reported on this cluster's configuration only
# (prefix caching on), i.e. the replays produced here. The first replay of the batch-A arms was
# produced on the other cluster without prefix caching and is dropped once local replays exist.
# Per frame; flip a frame to True once every batch-A 9B arm has >=1 local replay passing the
# coverage gate there. test flipped 2026-09-17 20:30 (all seven arms replayed locally on ft1).
# all waits for the tr1 halves of the q9b_tr1_* r2 lanes.
NINE_B_LOCAL_ONLY = {"test": True, "all": False}
A9, A9W = "actor_qwen35_9b", "actor_qwen35_9b_w4b"
POOL_ZERO_ADAPTER = False

ROWS = {
 # Two production batches per block, and they are NOT interchangeable: the older batch predates
 # prefix caching and ran on the other cluster. Measured on the same 95 test slots the two
 # no-memory arms differ by 7.4 points of resolve on 9B and 3.2 on 27B, so every memory arm is
 # read against the no-memory arm of its own batch and the batches are never pooled.
 # value per frame = LIST OF SOURCE GROUPS; each group is a list of halves merged into one replay.
 "9B": [
  ("seq",      {"test": [[(A9, "seq")]],          # 2026-09-23 user: earlier batch only (hidden row); the visible 9B
                "all":  [[(A9, "seq")]]}),          # reference is seq2 = later batch (ft1_actor9 / tr1_actor9)
  ("rawcat",   {"test": [[(A9, "mem_rawcat")]],   "all": [[(A9, "mem_rawcat")]]}),
  ("factsall", {"test": [[(A9, "mem_factsall")]], "all": [[(A9, "mem_factsall")]]}),
  ("memop",    {"test": [[(A9, "mem_memop")]],    "all": [[(A9, "mem_memop")]]}),
  ("rbank",    {"test": [[(A9, "mem_rbank")]],    "all": [[(A9, "mem_rbank")]]}),
  ("v4self",   {"test": [[(A9, "mem_v4note")]],   "all": [[(A9, "mem_v4note")]]}),
  ("v4w4b",    {"test": [[(A9W, "mem_v4note")]],  "all": [[(A9W, "mem_v4note")]]}),
  ("seq2",     {"test": [[("ft1_actor9", "seq_a9base")]],
                "all":  [[("ft1_actor9", "seq_a9base"), ("tr1_actor9", "seq_a9base")]]}),
  ("rbankj",   {"test": [[("ft1_actor9", "mem_rbankj_a9base")]],
                "all":  [[("ft1_actor9", "mem_rbankj_a9base"), ("tr1_actor9", "mem_rbankj_a9base")]]}),
  ("v5w4b",    {"test": [[("ft1_actor9", "mem_v5knote_a9base")]],
                "all":  [[("ft1_actor9", "mem_v5knote_a9base"), ("tr1_actor9", "mem_v5knote_a9base")]]}),
  ("v5self",   {"test": [[("ft1_actor9", "mem_v5knote_a9self")]],
                "all":  [[("ft1_actor9", "mem_v5knote_a9self")]]}),
  # 2026-09-21 user: record the 9B trained actor (a9prov6c, step limit 100) at step 8 with its own zero-update control (step 0)
  ("a9s0seq",  {"test": [[("ft1_actor9", "seq_a9prov6cs0")]],              "all": []}),
  ("a9s0note", {"test": [[("ft1_actor9", "mem_v5knote_a9prov6cs0_self")]], "all": []}),
  ("a9s8seq",  {"test": [[("ft1_actor9", "seq_a9prov6cs8")]],              "all": []}),
  ("a9s8note", {"test": [[("ft1_actor9", "mem_v5knote_a9prov6cs8_self")]], "all": []}),
 ],
 "27B": [
  # 2026-09-17 user ruling: one no-memory row for 27B. Both 27B batches ran the same configuration
  # (same weights, window, step limit, prefix caching off in both) and their no-memory arms differ by
  # 3.2 points, inside the replay floor, so their replays are pooled. 9B is NOT pooled (7.4 points,
  # different cluster and caching) until its local-only switch flips.
  # 2026-09-18: the LoRA-path replays of the untrained actor (ft1_actor9, *_a27dvw2s0) are NOT pooled into
  # the untrained rows until the len98k control settles whether the 64K window moves the with-note arm.
  # Switch POOL_ZERO_ADAPTER to True to pool them (band evidence so far: 0.5 points between the paths).
  # 2026-09-23 user: on the test frame (Table 1) every 27B untrained row reads run-id ft1_ledger, one batch (lanes
  # run 2026-09-22/23, 98K window, native coder-next, RSM_LEDGER=1). Table 2 keeps the older sources because
  # ft1_ledger covers only the 48 test chains. There is no ft1_ledger oracle-label ReasoningBank arm, so rbank /
  # rbankB (hidden, footnote-only) have no test-frame source; the v4 rows have no ft1_ledger arm and stay on ft1_grpo.
  ("seq",      {"test": [[("ft1_ledger", "seq_w27b")]],
                "all":  [[("v3main100", "seq")], [("ft1_grpo", "seq_seqB"), ("tr1_grpo", "seq_seqB")]]}),
  ("rawcat",   {"test": [[("ft1_ledger", "mem_rawcat_w27b")]],   "all": [[("v3main100", "mem_rawcat")]]}),
  ("factsall", {"test": [[("ft1_ledger", "mem_factsall_w27b")]], "all": [[("v3main100", "mem_factsall")]]}),
  ("memop",    {"test": [[("ft1_ledger", "mem_memop_w27b")]],    "all": [[("v3main100", "mem_memop")]]}),
  ("rbank",    {"test": [],                                       "all": [[("v3main100", "mem_rbank")]]}),
  ("rbankj",   {"test": [[("ft1_ledger", "mem_rbankj_w27b")]],
                "all":  [[("ft1_grpo", "mem_rbankj"), ("tr1_grpo", "mem_rbankj")]]}),
  ("rbankB",   {"test": [],
                "all":  [[("ft1_grpo", "mem_rbank"), ("tr1_grpo", "mem_rbank")]]}),
  ("v4self",   {"test": [[("ft1_grpo", "mem_v4knote_w27b")]],
                "all":  [[("ft1_grpo", "mem_v4knote_w27b"), ("tr1_grpo", "mem_v4knote_w27b")]]}),
  ("v4w4b",    {"test": [[("ft1_grpo", "mem_v4knote_base4b")]],
                "all":  [[("ft1_grpo", "mem_v4knote_base4b"), ("tr1_grpo", "mem_v4knote_base4b")]]}),
  ("v5w4b",    {"test": [[("ft1_ledger", "mem_v5knote_base4b_w27b")]],
                "all":  [[("ft1_grpo", "mem_v5knote_v5base"), ("tr1_grpo", "mem_v5knote_v5base")]]}),
  # 2026-09-18 user request: record the trained actor (C3, dvw2 step 12) in the test-frame table. These
  # runs use the actor evaluation configuration (LoRA serving path, prefix caching on, 64K window), which
  # by itself moves the with-note arm by -4.5 points (see zero-update control), so the group carries its
  # own configuration-matched references and its change tags are read against a27s0seq, not seq.
  ("v5self",   {"test": [[("ft1_ledger", "mem_v5knote_w27b")]],
                "all":  [[("ft1_grpo", "mem_v5knote_w27b"), ("tr1_grpo", "mem_v5knote_w27b")]]}),
  # 2026-09-23 user: new row, the note written inside the actor's own session (v5inline), ft1_ledger only
  ("v5inline", {"test": [[("ft1_ledger", "mem_v5inline_w27b")]], "all": []}),
  ("a27q4seq",  {"test": [[("ft1_actor9", "seq_a27dvw2_q4s12")]],          "all": []}),
  ("a27q4note", {"test": [[("ft1_actor9", "mem_v5knote_a27dvw2_q4s12")]],  "all": []}),
  # dvw3 (success-only R=res*(250-steps)) step 12; the 64K-window runs. Switch to the _len98k arms once they land.
  ("a27d3seq",  {"test": [[("ft1_actor9", "seq_a27dvw3s12")]],              "all": []}),
  ("a27d3note", {"test": [[("ft1_actor9", "mem_v5knote_a27dvw3s12")]],      "all": []}),
  ("a27p6note", {"test": [[("ft1_actor9", "mem_v5knote_a27prov6bs4_self")]], "all": []}),   # 2026-09-21 user: record prov6b s4 (98K, self-written notes)
  # 2026-09-23 user: the trained-actor block is read around prov6b s4. Same run-id ft1_actor9 (LoRA serving path,
  # 98K window, self-written notes): the zero-update controls (dvw6b step 0, 12 replays each), prov6b s4 without
  # memory, and dvw6b s4 as the ablation without provenance credit routing (no .dmg0920/.void replay among its three).
  ("a27s0seq",  {"test": [[("ft1_actor9", "seq_a27dvw6bs0")]],               "all": []}),
  ("a27s0note", {"test": [[("ft1_actor9", "mem_v5knote_a27dvw6bs0_self")]],  "all": []}),
  ("a27p6seq",  {"test": [[("ft1_actor9", "seq_a27prov6bs4")]],              "all": []}),
  ("a27d6note", {"test": [[("ft1_actor9", "mem_v5knote_a27dvw6bs4_self")]],  "all": []}),
 ],
}
STRICT_FULL_RUNS = {"ft1_ledger"}   # replays of these runs count only at full coverage of the frame (96/96 on test)
TAG_BASE = {}            # every tag is read against the block's no-memory row
NO_TAG = {"seq", "seq2"}
ORDER = ["seq", "rawcat", "factsall", "memop", "rbank", "seq2", "rbankj", "rbankB", "v4self", "v4w4b", "v5w4b",
         "v5self", "v5inline", "a27q4seq", "a27q4note", "a27d3seq", "a27d3note",
         "a27s0seq", "a27s0note", "a27p6seq", "a27p6note", "a27d6note",
         "a9s0seq", "a9s0note", "a9s8seq", "a9s8note"]
# 2026-09-17 user ruling: the ReasoningBank row is its official method, i.e. LLM-judge labels. The
# oracle-label variants (rbank, rbankB) stay in ROWS so the footnote can quote them, but are not printed
# for 27B. 9B keeps printing its oracle-label row in the two-batch layout until its judge-label runs land.
GROUPS_1BATCH = [("Ref.", ["seq", "rawcat"]),
                 (r"\shortstack[l]{Open-source\\memory}", ["factsall", "memop", "rbankj"]),
                 (r"\textbf{Ours}", ["v4self", "v4w4b", "v5w4b", "v5self", "v5inline"])]
GROUPS_9B = [("Ref.", ["seq", "seq2", "rawcat"]),
             (r"\shortstack[l]{Open-source\\memory}", ["factsall", "memop", "rbank", "rbankj"]),
             (r"\textbf{Ours}", ["v4self", "v4w4b", "v5w4b", "v5self"])]
GROUPS_2BATCH = [("Ref.", ["seq", "rawcat"]),
                 (r"\shortstack[l]{Open-source\\memory}", ["factsall", "memop", "rbank"]),
                 (r"\textbf{Ours}", ["v4self", "v4w4b"]),
                 (r"\shortstack[l]{Later batch:\\ref., open-source}", ["seq2", "rbankj"]),
                 (r"\shortstack[l]{\textbf{Ours}\\(later batch)}", ["v5w4b", "v5self"])]


# 2026-09-23 user: visible order = untrained controls, prov6b s4 without / with note (Ours), dvw6b s4 ablation. The
# dvw2/dvw3 rows (4B-written notes, 64K window) stay in the group but are hidden on the test frame (HIDE_ROWS).
GROUPS_C3 = [(r"\shortstack[l]{\textbf{Ours}\\(trained actor)}", ["a27s0seq", "a27s0note", "a27p6seq", "a27p6note", "a27d6note",
                                                                   "a27q4seq", "a27q4note", "a27d3seq", "a27d3note"])]


GROUPS_C3_9B = [(r"\shortstack[l]{\textbf{Ours}\\(trained actor)}", ["a9s0seq", "a9s0note", "a9s8seq", "a9s8note"])]


def groups(block, fr):
    if block == "27B":
        return GROUPS_1BATCH + (GROUPS_C3 if fr == "test" else [])
    # 2026-09-23 user (morning talk): one 9B layout on both frames; the visible reference and ReasoningBank are the
    # later-batch runs (seq2 / rbankj); the earlier-batch seq and the oracle-label rbank stay in source as hidden rows.
    return GROUPS_9B + (GROUPS_C3_9B if fr == "test" else [])

RLAB = {"seq": "No memory (seq)", "rawcat": "Raw concat (ceiling)",
        "seq2": "No memory (seq)",
        "factsall": r"Mem0$^\dagger$ {\scriptsize(\texttt{factsall})}",
        "memop": r"MemOp-prompt$^\S$", "rbank": r"ReasoningBank$^\P$ {\scriptsize(oracle labels)}",
        "rbankj": r"ReasoningBank$^\P$", "rbankB": r"ReasoningBank$^\P$ {\scriptsize(oracle labels)}",
        # 2026-09-17 user request: short labels. (self) = actor writes its own note; (4B) = separate,
        # untrained Qwen 4B writer; both spelled out in the footnote.
        "v4self": r"\textbf{Fused-v4 (self)}",
        "v4w4b": r"\textbf{Fused-v4 (4B)}",
        "v5w4b": r"\textbf{Fused-v5 (4B)}",
        "v5self": r"\textbf{Fused-v5 (self)}",
        "v5inline": r"\textbf{Fused-v5 (self, in-session)}",
        "a27q4seq": r"No memory, actor dvw2 s12$^{\ast}$",
        "a27q4note": r"\textbf{Fused-v5 (4B), actor dvw2 s12}$^{\ast}$",
        "a27d3seq": r"No memory, actor dvw3 s12$^{\ast}$",
        "a27d3note": r"\textbf{Fused-v5 (4B), actor dvw3 s12}$^{\ast}$",
        "a27p6note": r"\textbf{Fused-v5 (self), actor prov6b s4}",
        "a27s0seq": r"No memory, actor untrained (same config)",
        "a27s0note": r"Fused-v5 (self), actor untrained (same config)",
        "a27p6seq": r"No memory, actor prov6b s4",
        "a27d6note": r"Fused-v5 (self), actor dvw6b s4 {\scriptsize(w/o provenance credit)}",
        "a9s0seq": r"No memory, actor untrained (LoRA path)", "a9s0note": r"Fused-v5 (self), actor untrained (LoRA path)",
        "a9s8seq": r"No memory, actor a9prov6c s8", "a9s8note": r"\textbf{Fused-v5 (self), actor a9prov6c s8}"}
RLAB_OVR = {("test", "27B", "rbankj"): r"ReasoningBank$^\P$ {\scriptsize(judge labels)}",
            ("test", "9B", "seq"): "No memory (seq), earlier batch", ("all", "9B", "seq"): "No memory (seq), earlier batch"}
PLAIN = {"seq": "seq", "rawcat": "raw concat", "seq2": "seq (later batch)", "factsall": "Mem0",
         "memop": "MemOp-prompt", "rbank": "ReasoningBank oracle-label variant", "rbankj": "ReasoningBank", "rbankB": "ReasoningBank oracle-label variant (later batch)", "v4self": "Fused v4 (self-written)",
         "v4w4b": "Fused v4 $+$ 4B writer", "v5w4b": "Fused v5 $+$ 4B writer",
         "v5self": "Fused v5 (self-written)", "v5inline": "Fused v5 (self-written, in-session)",
         "a27q4seq": "no memory, actor dvw2 s12", "a27q4note": "Fused v5, actor dvw2 s12",
         "a27d3seq": "no memory, actor dvw3 s12", "a27d3note": "Fused v5, actor dvw3 s12",
         "a27p6note": "Fused v5 (self), actor prov6b s4",
         "a27s0seq": "no memory, actor untrained (same config)", "a27s0note": "Fused v5 (self), actor untrained (same config)",
         "a27p6seq": "no memory, actor prov6b s4", "a27d6note": "Fused v5 (self), actor dvw6b s4",
         "a9s0seq": "no memory, untrained LoRA path", "a9s0note": "Fused v5 (self), untrained LoRA path",
         "a9s8seq": "no memory, actor a9prov6c s8", "a9s8note": "Fused v5 (self), actor a9prov6c s8"}
# Writer / Note / Injected (K); carried over for the rows the earlier single-run tables printed.
COST = {
 "test": {
  "9B": {"seq": ("--", "--", "--"), "seq2": ("--", "--", "--"), "rawcat": ("--", r"\mmc{32.4--33.5}{32.9--34.3}", r"\mmc{8.23}{8.23}"),
         "factsall": (r"\mmc{36.36}{33.88}", r"\mmc{0.04}{0.03}$^{\sharp}$", r"\mmc{0.27}{0.25}"),
         "memop": (r"\mmc{36.48}{34.25}", r"\mmc{0.18}{0.19}$^{\sharp}$", r"\mmc{0.42}{0.42}"),
         "rbank": (r"\mmc{35.33}{34.88}", r"\mmc{0.36}{0.35}", r"\mmc{0.60}{0.52}"),
         "v4self": (r"\mmc{35.02}{34.36}", r"\mmc{0.24}{0.24}", r"\mmc{0.39}{0.35}"),
         "v4w4b": (r"\mmc{37.03}{35.03}", r"\mmc{0.23}{0.24}", r"\mmc{0.37}{0.35}")},
  "27B": {"seq": ("0", "0", "--"), "seq2": ("0", "0", "--"), "rawcat": ("--", "--", r"\mmc{7.08}{7.21}"),
          "factsall": (r"\mmc{18.19}{13.21}", r"\mmc{0.06}{0.04}$^{\sharp}$", r"\mmc{0.41}{0.21}"),
          "memop": (r"\mmc{17.43}{12.65}", r"\mmc{0.23}{0.22}$^{\sharp}$", r"\mmc{0.55}{0.56}"),
          "rbank": (r"\mmc{18.16}{13.19}", r"\mmc{0.38}{0.38}", r"\mmc{0.65}{0.62}"),
          "v4self": (r"\mmc{18.05}{13.02}", r"\mmc{0.28}{0.28}", r"\mmc{0.45}{0.42}"),
          "v4w4b": (r"\mmc{17.97}{12.72}", r"\mmc{0.28}{0.27}", r"\mmc{0.44}{0.42}")},
 },
 "all": {
  "9B": {"seq": ("--", "--", "--"), "seq2": ("--", "--", "--"), "rawcat": ("--", r"\mmc{31.3--32.4}{31.7--33.1}", r"\mmc{8.50}{8.39}"),
         "factsall": (r"\mmc{35.29}{33.83}", r"\mmc{0.03}{0.01}$^{\sharp}$", r"\mmc{0.25}{0.23}"),
         "memop": (r"\mmc{35.75}{33.10}", r"\mmc{0.18}{0.18}$^{\sharp}$", r"\mmc{0.42}{0.41}"),
         "rbank": (r"\mmc{35.99}{34.11}", r"\mmc{0.36}{0.35}", r"\mmc{0.59}{0.54}"),
         "v4self": (r"\mmc{33.51}{32.03}", r"\mmc{0.25}{0.24}", r"\mmc{0.40}{0.39}"),
         "v4w4b": (r"\mmc{35.74}{33.88}", r"\mmc{0.26}{0.24}", r"\mmc{0.42}{0.38}")},
  "27B": {"seq": ("0", "0", "--"), "seq2": ("0", "0", "--"), "rawcat": ("--", "--", r"\mmc{7.10}{7.26}"),
          "factsall": (r"\mmc{17.48}{12.29}", r"\mmc{0.06}{0.05}$^{\sharp}$", r"\mmc{0.34}{0.23}"),
          "memop": (r"\mmc{17.25}{11.53}", r"\mmc{0.23}{0.22}$^{\sharp}$", r"\mmc{0.55}{0.54}"),
          "rbank": (r"\mmc{16.61}{12.67}", r"\mmc{0.38}{0.39}", r"\mmc{0.65}{0.63}"),
          "v4self": (r"\mmc{16.81}{11.93}", r"\mmc{0.28}{0.29}", r"\mmc{0.45}{0.42}"),
          "v4w4b": (r"\mmc{17.14}{11.76}", r"\mmc{0.29}{0.28}", r"\mmc{0.46}{0.42}")},
 },
}
BLOCKHDR = {"9B": r"\textbf{Actor: Qwen3.5-9B (small) $\cdot$ harness: mini-swe-agent}",
            "27B": r"\textbf{Actor: Qwen3.8-27B-FP8 (main) $\cdot$ harness: mini-swe-agent}"}


def chains(fr):
    return {l.strip() for l in open(R / f"data/chains/{FRAMES[fr]}") if l.strip()}


def disc(run, base):
    pat = re.compile(r"^" + re.escape(base) + r"(_r\d+)?$")
    got = set()
    for d in (T3.RUNS / f"{run}").glob(f"*__{base}*"):
        nm = d.name.rsplit("__", 1)[1]
        if pat.match(nm):
            got.add(nm[len(base):])
    return sorted(got, key=lambda s: (len(s), s))


WIN = {}   # replay name -> (first, last) results.jsonl mtime over its chain dirs, for the console provenance line


def window(run, arm):
    import datetime as dt
    ts = [f.stat().st_mtime for f in (T3.RUNS / f"{run}").glob(f"*__{arm}/results.jsonl")]
    fmt = lambda t: dt.datetime.fromtimestamp(t).strftime("%m-%d %H:%M")
    return (fmt(min(ts)), fmt(max(ts))) if ts else ("?", "?")


def collect(block, fr):
    T3.TEST = chains(fr)
    out = []
    for key, sources in ROWS[block]:
        reps = []
        for halves in sources[fr]:
            for suf in disc(*halves[0]):
                if NINE_B_LOCAL_ONLY[fr] and block == "9B" and halves[0][0] in (A9, A9W) and suf == "":
                    continue   # other-cluster r1, not this configuration
                if (not NINE_B_LOCAL_ONLY[fr]) and block == "9B" and key == "seq" and halves[0][0] == "ft1_actor9":
                    continue   # two-batch layout: batch-B baseline lives in the seq2 row
                merged = {}
                for run, base in halves:
                    merged.update(T3.load(run, base + suf))
                if merged:
                    nm = "+".join(f"{r}:{b}{suf}" for r, b in halves)
                    ws = [window(r, b + suf) for r, b in halves]
                    WIN[nm] = (min(w[0] for w in ws), max(w[1] for w in ws))
                    reps.append((nm, merged))
        out.append((key, reps))
    return out


SEL = [("L", lambda r: r["linked"]), ("U", lambda r: not r["linked"]), ("O", lambda r: True)]


def stratstats(rows, keys, sel):
    ks = [k for k in keys if sel(rows[k])]
    if not ks:
        return None
    t = [rows[k]["ktok"] for k in ks if rows[k]["ktok"]]
    res = 100 * st.mean(1 if rows[k]["res"] else 0 for k in ks)
    tok = st.mean(t) if t else None
    return dict(res=res,
                steps=st.mean(rows[k]["steps"] for k in ks),
                tok=tok,
                # 2026-09-18 user request: token efficiency, resolve points bought per thousand actor
                # tokens. Formed inside the replay's own frame summary, so the table's mean and SD run
                # over per-replay ratios exactly as they do for the other cells (never a ratio of means).
                eff=(res / tok if tok else None),
                # per-replay medians; the table's second line is their mean over replays
                steps_med=st.median(rows[k]["steps"] for k in ks),
                tok_med=(st.median(t) if t else None))


def analyse(block, fr):
    coll = collect(block, fr)
    # coverage is judged against the frame's own slot count, never against whatever happens to be
    # on disk: otherwise a frame whose training half has not been run yet silently collapses to
    # its test half and every row passes the gate.
    denom = {"test": 96, "all": 200}[fr]
    kept, drop, dashed = [], [], []
    def passes(n, d):
        strict = any(part.split(":")[0] in STRICT_FULL_RUNS for part in n.split("+"))
        return len(d) >= denom if strict else len(d) / denom >= MIN_COV
    for key, reps in coll:
        ok = [(n, d) for n, d in reps if passes(n, d)]
        drop += [(key, n, len(d)) for n, d in reps if not passes(n, d)]
        (kept if ok else dashed).append((key, ok if ok else [(n, len(d)) for n, d in reps]))
    if not kept:   # nothing on this frame passes the coverage gate yet
        return dict(denom=denom, nslots=0, nL=0, nU=0, rows={}, dashed=[k for k, _ in dashed], dropped=drop)
    keys = sorted(set.intersection(*[set(d) for _, reps in kept for _, d in reps]))
    nL = sum(1 for k in keys if kept[0][1][0][1][k]["linked"])
    rows = {}
    for key, reps in kept:
        per = [{nm: stratstats(d, keys, sel) for nm, sel in SEL} for _, d in reps]
        cells = {}
        for nm, _ in SEL:
            cells[nm] = {}
            for met in ("res", "steps", "tok", "eff", "steps_med", "tok_med"):
                vs = [p[nm][met] for p in per if p[nm] and p[nm][met] is not None]
                cells[nm][met] = (None, None) if not vs else \
                    (st.mean(vs), st.stdev(vs) if len(vs) >= 2 else None)
        inj = [st.mean([d[k]["inj"] for k in keys if d[k]["inj"]]) for _, d in reps
               if any(d[k]["inj"] for k in keys)]
        rows[key] = dict(n=len(reps), cells=cells, reps=[[n, len(d)] for n, d in reps],
                         inj=(st.mean(inj) if inj else None))
    return dict(denom=denom, nslots=len(keys), nL=nL, nU=len(keys) - nL,
                rows=rows, dashed=[k for k, _ in dashed], dropped=drop)


def num(m, s, d=1, tag="", med=None):
    """2026-09-17 user request: line 1 = mean with the across-replay SD as a subscript, then the change tag;
    line 2 (steps and Tok only) = the per-replay median, averaged over replays, in parentheses."""
    if m is None:
        return "--"
    l1 = f"{m:.{d}f}" + ("" if s is None else rf"\sdsub{{{s:.{d}f}}}") + tag
    return l1 if med is None else rf"\mmc{{{l1}}}{{{med:.{d}f}}}"


def delta_tag(m, base, met):
    """2026-09-17 user request: every non-baseline cell carries its relative change against the no-memory
    row of the same block and frame, as a tiny superscript; blue (\dg) when the change is an improvement,
    light blue (\dl) when it is a deterioration. Resolve and Res./Tok: higher is better. Steps and Tok: lower."""
    if m is None or base is None or base == 0:
        return ""
    pct = 100.0 * (m - base) / base
    good = pct > 0 if met in ("res", "eff") else pct < 0
    # \hang{} gives the tag zero width, so the black value stays centred in its column and the tag hangs
    # to its right.
    if abs(pct) < 0.5:
        return r"\hang{\dz{0\%}}"
    sign = "+" if pct > 0 else r"\textminus"      # text mode: a real minus, not a hyphen
    return rf"\hang{{\{'dg' if good else 'dl'}{{{sign}{abs(pct):.0f}\%}}}}"


RBJ = {"27B": ("mem_rbankj", {"test": ["ft1_grpo"], "all": ["ft1_grpo", "tr1_grpo"]}),
       "9B":  ("mem_rbankj_a9base", {"test": ["ft1_actor9"], "all": ["ft1_actor9", "tr1_actor9"]})}
# 2026-09-23: the Table 1 27B ReasoningBank row reads ft1_ledger, so its Writer cell reads that arm's calls too.
# judge_stats (quoted in both footnotes) keeps reading the all-frame ft1_grpo/tr1_grpo sessions.
RBJ_OVR = {("27B", "test"): ("mem_rbankj_w27b", ["ft1_ledger"])}


def rbj_dirs(block, fr):
    import glob
    arm, runs = RBJ[block]
    runs = dict(runs)
    if (block, fr) in RBJ_OVR:
        arm, runs[fr] = RBJ_OVR[(block, fr)]
    ok = chains(fr)
    pat = re.compile(r"__" + re.escape(arm) + r"(_r\d+)?$")
    return [d for run in runs[fr] for d in glob.glob(str(T3.RUNS / f"{run}/*__{arm}*"))
            if pat.search(d) and pathlib.Path(d).name.split("__")[0] in ok]


def writer_k(block, fr):
    """mean and median writer-side tokens (K) per session of the judge-label ReasoningBank arm: judge call plus
    extraction call(s), token counts as returned by the serving engine."""
    per = {}
    for d in rbj_dirs(block, fr):
        w = pathlib.Path(d) / "writer_calls.jsonl"
        if not w.exists():
            continue
        for l in open(w):
            try:
                c = json.loads(l)
            except Exception:
                continue
            per[(d, c.get("order"))] = per.get((d, c.get("order")), 0) + (c.get("total_tokens") or 0)
    v = [x / 1000 for x in per.values() if x]
    return (st.mean(v), st.median(v)) if v else None


def judge_stats(block):
    """official LLM judge against the hidden-test verdict, over every judge-label session on disk."""
    n = agree = js = gs = 0
    jt = et = 0
    for d in rbj_dirs(block, "all"):
        f = pathlib.Path(d) / "results.jsonl"
        if f.exists():
            for l in open(f):
                try:
                    r = json.loads(l)
                except Exception:
                    continue
                if r.get("rb_judge") is None or r.get("rb_judge_gt") is None:
                    continue
                n += 1; agree += bool(r["rb_judge"]) == bool(r["rb_judge_gt"])
                js += bool(r["rb_judge"]); gs += bool(r["rb_judge_gt"])
        w = pathlib.Path(d) / "writer_calls.jsonl"
        if w.exists():
            for l in open(w):
                try:
                    c = json.loads(l)
                except Exception:
                    continue
                if c.get("kind") == "judge":
                    jt += c.get("total_tokens") or 0
                else:
                    et += c.get("total_tokens") or 0
    if not n:
        return None
    return dict(n=n, acc=100 * agree / n, judge_succ=100 * js / n, gt_succ=100 * gs / n,
                mult=(jt + et) / et if et else None)


def cost(fr, block, key, D):
    if key in COST[fr][block]:
        return COST[fr][block][key]
    row = D[fr][block]["rows"].get(key)
    if not row:
        return ("--", "--", "--")
    inj = row.get("inj")
    w = writer_k(block, fr) if key == "rbankj" else None
    return (rf"\mmc{{{w[0]:.2f}}}{{{w[1]:.2f}}}" if w else "--", "--", f"{inj:.2f}" if inj else "--")


HEAD = r"""\begin{table}[!t]
\centering
\small
\setlength{\tabcolsep}{3.5pt}
\caption{CAPTION}
\label{LABEL}
\resizebox{\linewidth}{!}{
\begin{tabular}{l|l|CCC|CCC|CCCC|MMM}
\toprule
\multicolumn{1}{c|}{\multirow{2}{*}{\textbf{Type}}} & \multicolumn{1}{c|}{\multirow{2}{*}{\textbf{Memory layer}}}
& \multicolumn{3}{c|}{\textbf{Linked} (dependency present)}
& \multicolumn{3}{c|}{\textbf{Unlinked} (independent)}
& \multicolumn{4}{c|}{\textbf{Overall}}
& \multicolumn{3}{c}{\textbf{Memory-side cost}} \\
\cmidrule(lr){3-5}\cmidrule(lr){6-8}\cmidrule(lr){9-12}\cmidrule(lr){13-15}
\multicolumn{1}{c|}{} & \multicolumn{1}{c|}{}
& {\scriptsize\shortstack{\textbf{Resolve}\\\textbf{(\%)} $\uparrow$}} & {\scriptsize\shortstack{\textbf{Steps}\\\textbf{mean (med.)} $\downarrow$}} & {\scriptsize\shortstack{\textbf{Tok (K)}\\\textbf{mean (med.)} $\downarrow$}}
& {\scriptsize\shortstack{\textbf{Resolve}\\\textbf{(\%)} $\uparrow$}} & {\scriptsize\shortstack{\textbf{Steps}\\\textbf{mean (med.)} $\downarrow$}} & {\scriptsize\shortstack{\textbf{Tok (K)}\\\textbf{mean (med.)} $\downarrow$}}
& {\scriptsize\shortstack{\textbf{Resolve}\\\textbf{(\%)} $\uparrow$}} & {\scriptsize\shortstack{\textbf{Steps}\\\textbf{mean (med.)} $\downarrow$}} & {\scriptsize\shortstack{\textbf{Tok (K)}\\\textbf{mean (med.)} $\downarrow$}} & {\scriptsize\shortstack{\textbf{Res./Tok}\\\textbf{(pp per K)} $\uparrow$}}
& {\scriptsize\shortstack{\textbf{Writer}\\\textbf{(K)}}} & {\scriptsize\shortstack{\textbf{Note}\\\textbf{(K)}}} & {\scriptsize\shortstack{\textbf{Injected}\\\textbf{(K)}}} \\"""


# 2026-09-18: the Overall group carries a fourth cell, resolve per K actor tokens; the other two strata keep three.
BODY = [(s, m) for s in ("L", "U") for m in ("res", "steps", "tok")] + \
       [("O", m) for m in ("res", "steps", "tok", "eff")]


HIDE_ROWS_BLOCK = {"9B": {"seq", "rbank"}}   # 2026-09-23 user: earlier-batch no-memory + oracle-label ReasoningBank, source only
HIDE_ROWS = {"all": {"v4self", "v4w4b"},
             # 2026-09-23 user: the dvw2/dvw3 rows (4B writer, 64K, older configuration) are kept as comments on Table 1
             "test": {"a27q4seq", "a27q4note", "a27d3seq", "a27d3note"}}   # 2026-09-21 user: keep the two Fused-v4 rows in the LaTeX of Table 2 but do not render them


BLOCKS = OUT / "blocks"


# 2026-09-23 user: the DeepSeek block is kept in the Table 1 source as "% [hidden]" comment lines but not rendered
# (its ledger rerun on Bouchet is pending); Table 2 still renders it. Flip a frame to True to render it again.
DS_RENDER = {"test": False, "all": True}


def ext_block(fr, part):
    """2026-09-23: externally delivered DeepSeek block / footnote sentence, spliced verbatim (see module docstring)."""
    f = BLOCKS / f"deepseek_{fr}_{part}.tex"
    if not f.exists():
        return f"% DeepSeek block file missing: {f}" + ("\n" if part == "footnote" else "")
    txt = f.read_text().rstrip("\n")
    if not DS_RENDER.get(fr, True):
        txt = "\n".join(l if l.startswith("%") else "% [hidden] " + l for l in txt.splitlines())
        return "% [hidden] DeepSeek block (DS_RENDER['" + fr + "'] is False)\n" + txt + "\n"
    return txt + ("\n" if part == "footnote" else "")


def ds_slots(fr):
    """caption clause for the DeepSeek block, parsed from the '% DS_SLOTS n= linked= unlinked=' line of its footnote file."""
    f = BLOCKS / f"deepseek_{fr}_footnote.tex"
    if not DS_RENDER.get(fr, True):
        return ""
    m = re.search(r"%\s*DS_SLOTS\s+n=(\d+)\s+linked=(\d+)\s+unlinked=(\d+)", f.read_text()) if f.exists() else None
    return (rf", and ${m.group(1)}$ slots (${m.group(2)}$ linked / ${m.group(3)}$ unlinked) for the DeepSeek block"
            if m else "")


def build(fr, label, caption, footnote, D):
    out = [HEAD.replace("CAPTION", caption).replace("LABEL", label)]
    for block in ("9B", "27B"):
        out += [r"\midrule", r"\multicolumn{15}{c}{\cellcolor{gray!15}" + BLOCKHDR[block] + r"} \\", r"\midrule"]
        first = True
        for gi, (lead, keys) in enumerate(groups(block, fr)):
            keys = [k for k in keys if any(kk == k for kk, _ in ROWS[block])
                    and D[fr][block]["rows"].get(k) is not None]   # a row with no data yet is omitted
            if not keys:
                continue
            # 2026-09-21: rows in HIDE_ROWS[fr] stay in the .tex as comments (not rendered); they still take part
            # in the common-slot intersection, so the visible numbers do not move when they are hidden.
            vis = [k for k in keys if k not in HIDE_ROWS.get(fr, ()) and k not in HIDE_ROWS_BLOCK.get(block, ())]
            if vis:
                if not first:
                    out.append(r"\cmidrule{1-15}")
                first = False
            jv = 0
            for k in keys:
                r = D[fr][block]["rows"].get(k)
                b = D[fr][block]["rows"].get(TAG_BASE.get(k, "seq2" if block == "9B" else "seq"))
                cells = " & ".join(["--"] * len(BODY)) if r is None else \
                    " & ".join(num(*r["cells"][s][m], d=2 if m == "eff" else 1,
                                   tag="" if (k in NO_TAG or b is None) else delta_tag(r["cells"][s][m][0], b["cells"][s][m][0], m),
                                   med=None if m in ("res", "eff") else r["cells"][s][m + "_med"][0])
                               for s, m in BODY)
                hidden = k not in vis
                head = (rf"\multirow{{{len(vis)}}}{{*}}{{{lead}}}" if (not hidden and jv == 0) else "")
                if not hidden:
                    jv += 1
                line = f"{head} & {RLAB_OVR.get((fr, block, k), RLAB[k])} & {cells} & " + " & ".join(cost(fr, block, k, D)) + r" \\"
                out.append(("% [hidden] " + line) if hidden else line)
    out.append(ext_block(fr, "block"))
    out += [r"\bottomrule", r"\end{tabular}}",
            r"\par\vspace{2pt}\begin{minipage}{\textwidth}\scriptsize " + footnote + r"\end{minipage}",
            r"\end{table}"]
    return "\n".join(out) + "\n"


def nstr(D, fr, block):
    shown = [k for _, ks_ in groups(block, fr) for k in ks_ if k not in globals().get('HIDE_ROWS', {}).get(fr, ())
             and k not in globals().get('HIDE_ROWS_BLOCK', {}).get(block, ())]
    ks = [k for k in ORDER if k in shown and any(kk == k and src.get(fr) for kk, src in ROWS[block])]   # no source on this frame -> not listed
    return ", ".join(f"{PLAIN[k]} $n{{=}}{D[fr][block]['rows'][k]['n']}$"
                     if k in D[fr][block]["rows"] else f"{PLAIN[k]} $n{{=}}0$" for k in ks)


def markers(D, fr):
    def cell(block, key):
        r = D[fr][block]["rows"].get(key)
        if not r:
            return None
        o = r["cells"]["O"]
        return rf"${o['res'][0]:.1f}$ resolve / ${o['steps'][0]:.1f}$ steps ($n{{=}}{r['n']}$)"
    rb = (r"$^\P$ReasoningBank with its official coding prompts, run inject-all. The success or failure label that "
          r"selects its extraction prompt comes from its official LLM-as-a-judge, the actor model judging its own "
          r"finished trajectory with no ground-truth reference. ")
    js = judge_stats("27B")
    if js:
        rb += (rf"Against the hidden-test verdict that judge is right on ${js['acc']:.0f}\%$ of ${js['n']}$ sessions "
               rf"of the $27$B actor, answering success on ${js['judge_succ']:.0f}\%$ where ${js['gt_succ']:.0f}\%$ "
               r"succeed")
        rb += (rf", and the extra call multiplies its writer-side tokens by ${js['mult']:.2f}$. " if js.get("mult")
               else ". ")
    up, lo = cell("27B", "rbank"), cell("27B", "rbankB")
    if up or lo:
        rb += (r"An oracle-label variant, which reads the hidden-test verdict of the finished session instead and is "
               r"therefore available to no deployed system, is not shown for the $27$B actor; it scores ")
        parts = []
        if up:
            parts.append(up + " in the run of the other open-source rows")
        if lo:
            parts.append(lo + " in the run of our own rows")
        rb += ", and ".join(parts) + ". "
    if not D[fr]["9B"]["rows"].get("rbankj"):
        rb += (r"The $9$B block still prints that oracle-label variant, marked as such, until its judge-label runs "
               r"complete. ")
    # 2026-09-23 user: one 9B layout, later batch as the reference on both frames
    r_old, r_new = D[fr]["9B"]["rows"].get("seq"), D[fr]["9B"]["rows"].get("seq2")
    agree = ("" if not (r_old and r_new) else
             rf" (overall resolve of the two no-memory runs on this frame: earlier batch ${r_old['cells']['O']['res'][0]:.1f}$, "
             rf"later batch ${r_new['cells']['O']['res'][0]:.1f}$)")
    if NINE_B_LOCAL_ONLY[fr]:
        nine = (r"Every $9$B row is a replay produced on this cluster with prefix caching on; the visible no-memory and "
                r"ReasoningBank rows are the later-batch runs (run-id \texttt{ft1\_actor9}) and every $9$B tag is read "
                r"against that no-memory row. The earlier-batch no-memory row and the oracle-label ReasoningBank row are "
                r"kept in the source only" + agree + r". The first replays of the raw-concat, Mem0, MemOp and v4 arms, "
                r"produced earlier on another cluster without prefix caching, are not pooled in because on the same $95$ "
                r"test slots their no-memory arm sits $7.4$ points above the local one. ")
    else:
        nine = (r"The visible no-memory and ReasoningBank rows of the $9$B block are the later-batch runs on this cluster "
                r"(run-ids \texttt{ft1\_actor9} and \texttt{tr1\_actor9}, prefix caching on) and every $9$B tag is read "
                r"against that no-memory row; raw concat, Mem0 and MemOp come from the earlier batch on our other cluster "
                r"with prefix caching off. The earlier-batch no-memory row and the oracle-label ReasoningBank row are kept "
                r"in the source only" + agree + r". ")
    c3 = ""
    shown = lambda k: D[fr]["27B"]["rows"].get(k) and k not in HIDE_ROWS.get(fr, ())
    if fr == "test" and (shown("a27q4note") or shown("a27d3note")):
        c3 = (r"$^{\ast}$Trained actor: the $27$B actor after $12$ policy updates of the cost-internalising "
              r"post-training, dvw2 (reward $\mathrm{res}\cdot(50-\mathrm{steps})$, NF4 base) or dvw3 (reward "
              r"$\mathrm{res}\cdot(250-\mathrm{steps})$, success-only), served through a LoRA adapter with prefix caching on and a $64$K "
              r"window. The untrained rows of this block are native-path runs with a $96$K window; across $136$ "
              r"replays of the untrained actor the two serving paths differ by $0.5$ points, and a window-matched "
              r"control is pending. ")
    if fr == "test" and shown("a27p6note"):
        c3 += (r"The trained-actor rows of the $27$B block all run in one configuration (LoRA serving path, prefix "
               r"caching on, a $98$K window, notes written by the actor itself). prov6b and dvw6b are the $27$B actor "
               r"after $4$ policy updates, with and without provenance credit routing; the two recipes also differ in "
               r"reward shaping and in the checkpoint's own settings, so the dvw6b row is not a single-variable ablation. "
               r"The two untrained rows are the "
               r"zero-update checkpoint of the same pipeline. Like every other row of the block, the trained-actor rows "
               r"carry superscripts read against the block's no-memory row, which ran in a different configuration. ")
    pool27 = ("" if fr == "test" else
              r"The $27$B block draws on two runs, but both used one configuration and their no-memory arms differ "
              r"by $3.2$ points, inside the replay floor, so the $27$B no-memory row pools the replays of both. ")
    return (nine + c3 + pool27 +
            r"$^\dagger$Mem0 extraction-union, official prompts on the local writer (zero external API; it injects "
            r"nothing where the store is empty). $^\S$MemOp's guidance document, official prompt on the local "
            r"writer. " + rb +
            r"$^\sharp$Note\,(K) cell whose per-note estimator is undefined (a rolling single document, or facts "
            r"produced only on the last write); the characters$/4000$ figure is kept there. Raw concat's Note is "
            r"printed as an interval because its last write is only partly recoverable.")


CONV = (r"\textbf{Metric convention.} Resolve, Steps and Tok\,(K) are recomputed from run artifacts with "
        r"\texttt{paper/tables/make\_main\_avg.py} on the conventions of \texttt{train-corpus/exam\_table3.py} "
        r"(order $\in\{2,3\}$, steps $=$ \texttt{n\_steps}, Tok\,(K) $=$ trajectory characters$/4000$, linked flag "
        r"\texttt{task\_link\_flags\_v2}), not with the emitter of the earlier single-run tables, which "
        r"additionally drops no-op grading slots and counts steps as tokenizer API calls; Tok\,(K) therefore runs "
        r"$7$--$9\%$ below the exact-tokenizer convention of those tables and the two are not interchangeable. "
        r"Memory-side cost columns are single-run values carried over unchanged and remain mean over (median), "
        r"except on the v5 rows and the ReasoningBank row of the lower batch, where \emph{Injected} is recomputed "
        r"here as characters$/4000$, \emph{Note} is not recomputed, and \emph{Writer} is given only for "
        r"ReasoningBank, as the serving engine's own token count over the judge and extraction calls of a session. "
        r"\emph{Res./Tok} divides the overall resolve rate by the overall Tok\,(K) of the same replay, so it reads "
        r"as resolved percentage points bought per thousand actor tokens and higher is better; it is dashed wherever "
        r"Tok\,(K) is.")
FLOOR = (r"Replay noise floor, measured from three replays of one fixed arm and checkpoint (\texttt{v5base}) on the "
         r"test frame, is $\pm2.6$ points of overall resolve, $\pm2.4$ steps and $\pm1.1$\,Tok\,(K); per stratum it "
         r"is $\pm2.8$/$\pm2.7$ points and $\pm3.1$/$\pm3.0$ steps. Differences smaller than that floor are not "
         r"separable from replay noise, whatever the printed $\pm$ says.")
DELTA = (r"\textbf{Our rows.} Fused-v4 and Fused-v5 are the two note templates; (self) means the actor "
         r"writes its own note at the boundary, (4B) that a separate, untrained Qwen3.5-4B writer does. "
         r"\textbf{Superscripts.} Every cell outside the no-memory row carries its relative change against the "
         r"no-memory row of the same block, as a percentage of that row's value; \dg{blue} marks an improvement "
         r"(higher resolve, fewer steps, fewer tokens) and \dl{light blue} a deterioration. ")
AGG = (DELTA + r"Each cell prints the mean over replays, with the across-replay sample standard deviation as a "
       r"subscript when two or more replays are complete; each replay is first reduced to its own frame summary, "
       r"and the mean and SD are taken over those per-replay summaries. A single-replay cell prints that run "
       r"alone. For Steps and Tok\,(K) the parenthesised second line is the per-replay median, averaged over "
       r"replays.")

if __name__ == "__main__":
    import os
    WOUT = pathlib.Path(os.environ.get("MMA_OUT", str(OUT)))   # output dir override for dry runs; block files still read from OUT/blocks
    D = {fr: {b: analyse(b, fr) for b in ("9B", "27B")} for fr in ("test", "all")}
    for fr in ("test", "all"):
        for b in ("9B", "27B"):
            a = D[fr][b]
            print(f"\n### frame={fr} block={b}: denom {a['denom']}, common {a['nslots']} "
                  f"(L {a['nL']} / U {a['nU']}); dashed {a['dashed']}")
            if a["dropped"]:
                print("   dropped(cov<95%): " + "; ".join(f"{k}{n}={c}" for k, n, c in a["dropped"]))
            for k in ORDER:
                r = a["rows"].get(k)
                if not r:
                    print(f"   {PLAIN[k]:<28} --")
                    continue
                f = lambda s, m: ("--" if r["cells"][s][m][0] is None else
                                  f"{r['cells'][s][m][0]:.1f}" + ("" if r["cells"][s][m][1] is None
                                                                  else f"+-{r['cells'][s][m][1]:.1f}"))
                print(f"   {PLAIN[k]:<28} n={r['n']}  " +
                      "  ".join(f"{f(s,m):>10}" for s, m in BODY))
                runs = sorted({p.split(":")[0] for nm, _ in r["reps"] for p in nm.split("+")})
                print(f"      ({','.join(runs)}, replays " + ", ".join(nm.split(":", 1)[1].split("+")[0] for nm, _ in r["reps"])
                      + ", results window " + (min(WIN[nm][0] for nm, _ in r["reps"]) + " .. " + max(WIN[nm][1] for nm, _ in r["reps"])
                                             if all(nm in WIN for nm, _ in r["reps"]) else "?") + ")")
    s9, s27 = D["test"]["9B"], D["test"]["27B"]
    cap_test = (r"\textbf{ChainSWE held-out test split, averaged over replays.} Frame is the $48$ test chains of "
                r"\texttt{split\_v2\_test}, chain-disjoint from the $51$ training chains; slots are the successor "
                r"positions at pos $\geq 2$. Arms are scored on the slots every replay of every arm in that block "
                rf"completed, which gives ${s9['nslots']}$ slots (${s9['nL']}$ linked / ${s9['nU']}$ unlinked) for "
                rf"the $9$B block and ${s27['nslots']}$ slots (${s27['nL']}$ linked / ${s27['nU']}$ unlinked) for "
                r"the $27$B block" + ds_slots("test") + ". " + AGG + " " + FLOOR + r" Absolute resolve is comparable only \emph{within} an "
                r"actor block and within a batch, against the no-memory row that shares its batch.")
    fn_test = (r"\textbf{Replays per row.} $9$B block --- " + nstr(D, "test", "9B") +
               r". $27$B block, run-id \texttt{ft1\_ledger} with lanes run 2026-09-22/23 except the rows named under "
               r"Provenance --- " +
               nstr(D, "test", "27B") + r". Every $n{=}1$ cell is a single run whose value moves by about the noise "
               r"floor between runs, so it should be read as provisional. " + "\n" + ext_block("test", "footnote") +
               r"\textbf{Provenance.} $9$B open-source and "
               r"v4 rows come from runs \texttt{actor\_qwen35\_9b} and \texttt{actor\_qwen35\_9b\_w4b}; the $9$B "
               r"v5 row and its no-memory baseline come from \texttt{ft1\_actor9}. $27$B reference, open-source and "
               r"v5 rows (including the in-session variant) and their no-memory baseline come from one batch, "
               r"\texttt{ft1\_ledger} ($98$K window, native serving path); the $27$B v4 rows come from "
               r"\texttt{ft1\_grpo} and the trained-actor rows from \texttt{ft1\_actor9}. " + CONV + " " + markers(D, "test"))
    a9, a27 = D["all"]["9B"], D["all"]["27B"]
    cap_all = (r"\textbf{ChainSWE full sample, averaged over replays.} Frame is all $100$ chains of the main pool, "
               r"the $48$ test chains and the $51$ training chains plus one chain outside both splits, slots at "
               r"pos $\geq 2$. Arms are scored on the slots every replay of every arm in that block completed, which "
               rf"gives ${a9['nslots']}$ slots (${a9['nL']}$ linked / ${a9['nU']}$ unlinked) for the $9$B block and "
               rf"${a27['nslots']}$ slots (${a27['nL']}$ linked / ${a27['nU']}$ unlinked) for the $27$B block" + ds_slots("all") + r". A "
               r"replay counts on this frame only when the arm has that replay over the test chains \emph{and} over "
               r"the training chains, since the two halves are run by separate lanes. " + AGG + " " + FLOOR +
               r" Absolute resolve is comparable only \emph{within} an actor block.")
    fn_all = (r"\textbf{Replays per row.} $9$B block --- " + nstr(D, "all", "9B") + r". $27$B block --- " +
              nstr(D, "all", "27B") + ". \n" + ext_block("all", "footnote") + r"\textbf{Dashed rows.} A row is dashed when no run of that arm spans this "
              r"frame: the actor-line runs (\texttt{ft1\_grpo}, \texttt{ft1\_actor9}) cover the test chains only, so "
              r"the $27$B \textbf{Fused v4 (self-written)} row, the $9$B v5 row and both no-memory rows of those "
              r"runs have no training-chain half; the v5 and v4$+$4B rows of the $27$B block do, through "
              r"\texttt{tr1\_grpo}. \textbf{Provenance.} $9$B rows come from \texttt{actor\_qwen35\_9b} and "
              r"\texttt{actor\_qwen35\_9b\_w4b}, each covering all $100$ chains; $27$B reference and open-source "
              r"rows from \texttt{v3main100}; the $27$B v4$+$4B and v5 rows join \texttt{ft1\_grpo} to "
              r"\texttt{tr1\_grpo}. Recomputed from artifacts the v4$+$4B row sits about $3$ points of overall "
              r"resolve below the corresponding row of the earlier single-run table, which was reported from the "
              r"second cluster's own run; the reference and open-source rows reproduce their earlier values to "
              r"within about one point. " + CONV + " " + markers(D, "all"))
    (WOUT / "main_test_avg.tex").write_text(build("test", "tab:main-test-avg", cap_test, fn_test, D))
    (WOUT / "main_all_avg.tex").write_text(build("all", "tab:main-all-avg", cap_all, fn_all, D))
    (WOUT / "main_avg_summary.json").write_text(json.dumps(D, indent=1))
    print("\nwrote", WOUT / "main_test_avg.tex", "and", WOUT / "main_all_avg.tex")
