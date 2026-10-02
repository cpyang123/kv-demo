#!/usr/bin/env python3
"""考场帧上的 Table-3 口径全臂对比。

列与 paper 的 tab:main 一致:Linked / Unlinked / Overall 三组,每组
  Resolve(%) · Steps mean(med) · Tok(K) mean(med);外加记忆侧成本 Injected(K)。
行 = frame_test_v1 上现有的全部臂。所有臂在**共有槽**上比较(order in {2,3})。
linked 旗标键为 "<chain>|<k>"(与 arm_strata_table.py 一致)。

用法: exam_table3.py [--frame test|train] [臂 ...]
  臂可写 "run:arm" 从别的 run 取(如 v3main100:seq)。
  --frame test (默认) = frame_test_v1,默认 run ft1_grpo;
  --frame train       = split_v2_train,默认 run tr1_grpo(GRPO 训练帧,in-sample)。
  2026-09-11:此前帧与默认 run 都写死成测试帧,训练帧根本出不了表。
"""
import re
import json, glob, sys, statistics as st, collections, pathlib
import os

# Path handling (analysis/ledger_table copy): the chainswe repo root is resolved from this file's location
# (analysis/ledger_table/ -> repo root) unless RSM_ROOT is set; run directories live under RUNS = <root>/runs
# unless RSM_RUNS is set. Chain lists and link flags are read from <root>/data/.
R = pathlib.Path(os.environ.get("RSM_ROOT") or pathlib.Path(__file__).resolve().parents[2])
RUNS = pathlib.Path(os.environ.get("RSM_RUNS") or (R / "runs"))
_argv = sys.argv[1:]
FRAME = "test"
if "--frame" in _argv:
    i = _argv.index("--frame"); FRAME = _argv[i + 1]; del _argv[i:i + 2]
_CH = {"test": "frame_test_v1_chains.txt", "train": "split_v2_train.txt"}[FRAME]
DEFRUN = {"test": "ft1_grpo", "train": "tr1_grpo"}[FRAME]
TEST = {l.strip() for l in open(R / f"data/chains/{_CH}") if l.strip()}
FLAGS = json.load(open(R / "data/task_link_flags_v2.json"))

DEFAULT = ["v3main100:seq", "mem_v4knote_w27b", "mem_v4knote_sonnet", "mem_v4knote_base4b",
           "mem_v4knote_sftv5cs", "mem_v4knote_v3bfs16", "mem_v4knote_v4bs2", "mem_v4knote_v4bs4",
           "mem_v4kverify_verify", "mem_v4kgate_gdist50", "mem_v4kgate_gsonnet", "mem_v4kgate_gbase50"]
LABEL = {"seq": "No memory (seq)", "w27b": "Fused v4 (27B self-written)", "sonnet": "Fused v4 + Sonnet writer",
         "base4b": "Fused v4 + 4B writer (untrained)", "sftv5cs": "Fused v4 + 4B SFT writer",
         "v3bfs16": "Fused v4 + 4B GRPO v3b s16", "v4bs2": "Fused v4 + 4B GRPO v4b s2",
         "v4bs4": "Fused v4 + 4B GRPO v4b s4", "verify": "Fused v4 + verify-first protocol",
         "gdist50": "Gate: distilled reader", "gsonnet": "Gate: Sonnet judge", "gbase50": "Gate: untrained 4B"}

def load(run, arm):
    rows = {}
    for d in glob.glob(str(RUNS / f"{run}/*__{arm}")):
        c = d.split("/")[-1].split("__")[0]
        if c not in TEST: continue
        f = pathlib.Path(d) / "results.jsonl"
        if not f.exists(): continue
        for l in open(f):
            if not l.strip(): continue
            try: r = json.loads(l)
            except Exception: continue
            k = r.get("order")
            if k not in (2, 3): continue
            t = glob.glob(f"{d}/traj_{k}_*.json"); ch = None
            if t:
                try: ch = sum(len(m.get("content", "") or "") for m in json.load(open(t[0]))["messages"])
                except Exception: ch = None
            rows[(c, k)] = dict(res=bool(r.get("resolved")), steps=r.get("n_steps", 0),
                                ktok=(ch / 4000 if ch else None),
                                inj=(r.get("injected_chars") or 0) / 4000,
                                linked=bool(FLAGS.get(f"{c}|{k}")))
    return rows

def cell(rows, keys, sel):
    ks = [k for k in keys if sel(rows[k])]
    if not ks: return "  --  ", "     --     ", "     --     "
    res = f"{100*st.mean(1 if rows[k]['res'] else 0 for k in ks):5.1f}"
    s = [rows[k]["steps"] for k in ks]
    t = [rows[k]["ktok"] for k in ks if rows[k]["ktok"]]
    return res, f"{st.mean(s):5.1f}({st.median(s):4.0f})", (f"{st.mean(t):5.1f}({st.median(t):4.1f})" if t else "     --     ")

def main():
    want = _argv or DEFAULT
    arms = {}
    for a in want:
        run, _, nm = a.rpartition(":")
        r = load(run or DEFRUN, nm)
        # 2026-09-13: 键改为去掉 mem_<memsys>_ 前缀的完整臂名(原 split("_")[-1] 会让 *_r2 臂互相覆盖)
        if r: arms[("seq" if nm == "seq" else re.sub(r"^mem_[A-Za-z0-9]+_", "", nm))] = r
        else: print(f"(无数据 {a})", file=sys.stderr)
    keys = sorted(set.intersection(*[set(v) for v in arms.values()]))
    nL = sum(1 for k in keys if arms[list(arms)[0]][k]["linked"])
    print(f"{_CH.replace('.txt','')} 共有槽 {len(keys)}(linked {nL} / unlinked {len(keys)-nL});臂 {len(arms)} 个;各臂已完成槽: "
          + " ".join(f"{a}:{len(v)}" for a, v in arms.items()) + "\n")
    hdr = f"{'Memory layer':<34}| {'Linked':^26}| {'Unlinked':^26}| {'Overall':^26}| Inj(K)"
    print(hdr); print(f"{'':<34}| " + "  ".join(f"{x:^8}" for x in ("Res%", "Steps", "Tok(K)")) + " | "
          + "  ".join(f"{x:^8}" for x in ("Res%", "Steps", "Tok(K)")) + " | "
          + "  ".join(f"{x:^8}" for x in ("Res%", "Steps", "Tok(K)")) + " |"); print("-" * 132)
    for a, rows in arms.items():
        L = cell(rows, keys, lambda r: r["linked"]); U = cell(rows, keys, lambda r: not r["linked"]); O = cell(rows, keys, lambda r: True)
        inj = st.mean(rows[k]["inj"] for k in keys)
        print(f"{LABEL.get(a, a):<34}|{L[0]} {L[1]} {L[2]} |{U[0]} {U[1]} {U[2]} |{O[0]} {O[1]} {O[2]} | {inj:5.2f}")
    print("\n口径:order in {2,3} 的后继槽;Tok(K)=轨迹字符/4000;Inj(K)=注入字符/4000;linked 旗标 task_link_flags_v2。")

if __name__ == "__main__":
    main()
