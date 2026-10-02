#!/usr/bin/env python3
"""台账三口径的逐臂汇总(2026-09-22,DESIGN_inline_ledger_0921.md §B.4)。

臂解析与帧/共有槽口径**复用 exam_table3.py / exam_table3_reps.py**(`run:base` 语法、
`^base(_r\\d+)?$` 重放合并、`--frame test|train`、共有槽交集、`--min-cov`),两个文件一字未动。

三口径(哪个当头条是出表的决定,不是代码的决定):
  ① raw        = Σ(prompt + completion)              部署无关、可跨系统比较
  ② cacheaware = Σ(uncached_prompt + completion)     本机这台服务器上的新算量(机制列)
  ③ priced     = prices.json 折算的美元              折算价,非实际支出

数据源优先级:`calls.jsonl`(RSM_LEDGER=1 的运行) -> 缺失时回落 `traj_*.json` 的 usage
(actor 侧,历史 run 可补账)+ `writer_calls.jsonl`(rbank/rbankj 的写手侧)。

**计数陷阱**(memory: chainswe-results-counting-trap):results.jsonl 无尾换行,一律逐行 strip
跳空行;行数核对请用 `grep -o '"order"' | wc -l`,不要跨文件 cat + wc -l。

用法:
  ledger_rollup.py [--frame test|train] [--min-cov 0.95] [--price-row opus5] <臂基名 ...>
  ledger_rollup.py --frame test ft1_grpo:seq_seqB ft1_grpo:mem_v5inline_a27base
"""
import collections
import glob
import json
import pathlib
import statistics as st
import sys

_argv = sys.argv[1:]
FRAME = "test"
if "--frame" in _argv:
    i = _argv.index("--frame"); FRAME = _argv[i + 1]; del _argv[i:i + 2]
MIN_COV = 0.95
if "--min-cov" in _argv:
    i = _argv.index("--min-cov"); MIN_COV = float(_argv[i + 1]); del _argv[i:i + 2]
PRICE_ROW = None
if "--price-row" in _argv:
    i = _argv.index("--price-row"); PRICE_ROW = _argv[i + 1]; del _argv[i:i + 2]

sys.argv = [sys.argv[0], "--frame", FRAME]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import exam_table3 as T3                        # noqa: E402
sys.path.insert(0, str(T3.R))                   # harness/ledger.py of the repo root (RSM_ROOT or ../..)
import exam_table3_reps as T3R                  # noqa: E402
from harness import ledger as L                 # noqa: E402
sys.argv = [sys.argv[0]] + _argv

R, DEFRUN, _CH, LABEL = T3.R, T3.DEFRUN, T3._CH, T3.LABEL
ORDERS = (2, 3)


def read_calls(d):
    """<chain_out>/calls.jsonl -> {slot: [row, ...]}。"""
    out = collections.defaultdict(list)
    f = pathlib.Path(d) / "calls.jsonl"
    if not f.exists():
        return out
    for l in open(f):
        l = l.strip()
        if not l:
            continue
        try:
            r = json.loads(l)
        except Exception:
            continue
        out[r.get("slot")].append(r)
    return out


def rows_from_traj(d, k):
    """回落源 A:traj_<k>_*.json 的 usage(actor)。历史 run 也能补账。"""
    rows = []
    for t in glob.glob(f"{d}/traj_{k}_*.json"):
        try:
            msgs = json.load(open(t))["messages"]
        except Exception:
            continue
        for m in msgs:
            u = (((m.get("extra") or {}).get("response") or {}) or {}).get("usage") or {}
            if not u:
                continue
            det = u.get("prompt_tokens_details") or {}
            pt = int(u.get("prompt_tokens") or 0)
            cc = int((det or {}).get("cached_tokens") or 0)
            rows.append({"role": "actor", "prompt_tokens": pt, "cached_tokens": min(cc, pt),
                         "uncached_prompt_tokens": pt - min(cc, pt),
                         "completion_tokens": int(u.get("completion_tokens") or 0),
                         "cached_src": "usage" if det else "none"})
        break
    return rows


def rows_from_writer_calls(d, k):
    """回落源 B:writer_calls.jsonl(rbank/rbankj 的写手与判官;无 cached 字段)。"""
    rows = []
    f = pathlib.Path(d) / "writer_calls.jsonl"
    if not f.exists():
        return rows
    for l in open(f):
        l = l.strip()
        if not l:
            continue
        try:
            r = json.loads(l)
        except Exception:
            continue
        if r.get("order") != k:
            continue
        pt = int(r.get("prompt_tokens") or 0)
        rows.append({"role": ("judge" if r.get("kind") == "judge" else "note_writer"),
                     "prompt_tokens": pt, "cached_tokens": 0, "uncached_prompt_tokens": pt,
                     "completion_tokens": int(r.get("completion_tokens") or 0),
                     "cached_src": "none"})
    return rows


def load_arm(run, arm):
    """-> {(chain, order): {**summarize(rows), src, mode, reason, note_chars, inline_hit}}"""
    out = {}
    for d in glob.glob(str(T3.RUNS / f"{run}/*__{arm}")):
        c = d.split("/")[-1].split("__")[0]
        if c not in T3.TEST:
            continue
        rf = pathlib.Path(d) / "results.jsonl"
        if not rf.exists():
            continue
        calls = read_calls(d)
        for l in open(rf):
            l = l.strip()
            if not l:
                continue
            try:
                r = json.loads(l)
            except Exception:
                continue
            k = r.get("order")
            if k not in ORDERS:
                continue
            rows, src = calls.get(k) or [], "calls.jsonl"
            if not rows:
                rows = rows_from_traj(d, k) + rows_from_writer_calls(d, k)
                src = "traj+writer_calls" if rows else "none"
            s = L.summarize(rows)
            s["src"] = src
            s["mode"] = r.get("note_write_mode")
            s["reason"] = r.get("note_write_reason")
            s["note_chars"] = r.get("note_chars")
            s["injected_tokens"] = r.get("injected_tokens")
            nt = [x for x in rows if x.get("note_turn")]
            s["inline_hit"] = (sum(x["cached_tokens"] for x in nt) / sum(x["prompt_tokens"] for x in nt)
                               if nt and sum(x["prompt_tokens"] for x in nt) else None)
            out[(c, k)] = s
    return out


def discover(run, base):
    import re
    pat = re.compile(r"^" + re.escape(base) + r"(_r\d+)?$")
    names = set()
    for d in (T3.RUNS / f"{run}").glob(f"*__{base}*"):
        nm = d.name.rsplit("__", 1)[1] if "__" in d.name else ""
        if pat.match(nm):
            names.add(nm)
    out = []
    for nm in sorted(names, key=lambda s: (len(s), s)):
        rows = load_arm(run, nm)
        if rows:
            out.append((nm, rows))
    return out


def main():
    want = _argv
    if not want:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        return 2
    if PRICE_ROW:
        import os
        os.environ["RSM_PRICE_ROW"] = PRICE_ROW
    groups = []
    for a in want:
        run, _, base = a.rpartition(":")
        reps = discover(run or DEFRUN, base)
        if not reps:
            print(f"(无数据 {a})", file=sys.stderr); continue
        groups.append((base, reps))
    if not groups:
        return 1
    denom = max(len(r) for _, reps in groups for _, r in reps)
    kept = []
    for key, reps in groups:
        ok = [(nm, r) for nm, r in reps if len(r) / denom >= MIN_COV]
        if ok:
            kept.append((key, ok))
        else:
            print(f"(基名 {key} 的重放全部低于覆盖阈值,已丢弃)", file=sys.stderr)
    if not kept:
        return 1
    keys = sorted(set.intersection(*[set(r) for _, reps in kept for _, r in reps]))
    print(f"{_CH.replace('.txt','')} 共有槽 {len(keys)};臂基名 {len(kept)} 个;"
          f"重放共 {sum(len(r) for _, r in kept)} 条;价表 {L.price_table_sha()} 行 {L.price_row()['_row']}")
    print()
    hdr = (f"{'Arm':<30}| {'raw K':>8} {'cacheK':>8} {'USD':>8} | "
           f"{'act K':>8} {'wri K':>8} {'oth K':>8} | {'InjK':>6} {'src':>16} {'hit':>5} n_rep")
    print(hdr); print("-" * len(hdr))
    for key, reps in kept:
        per = []
        for _, rows in reps:
            per.append({
                "raw": st.mean(rows[k]["tok_raw_total"] for k in keys) / 1000,
                "ca": st.mean(rows[k]["tok_cacheaware_total"] for k in keys) / 1000,
                "usd": st.mean(rows[k]["cost_usd_priced"] for k in keys),
                "act": st.mean(rows[k]["tok_actor_prompt"] + rows[k]["tok_actor_completion"] for k in keys) / 1000,
                "wri": st.mean(rows[k]["tok_writer_prompt"] + rows[k]["tok_writer_completion"] for k in keys) / 1000,
                "oth": st.mean(rows[k]["tok_memother_prompt"] + rows[k]["tok_memother_completion"] for k in keys) / 1000,
                "inj": st.mean((rows[k]["injected_tokens"] or 0) for k in keys) / 1000,
            })
        srcs = collections.Counter(rows[k]["src"] for _, rows in reps for k in keys)
        hits = [rows[k]["inline_hit"] for _, rows in reps for k in keys if rows[k]["inline_hit"] is not None]
        m = {f: st.mean(p[f] for p in per) for f in per[0]}
        print(f"{LABEL.get(key, key):<30}| {m['raw']:8.1f} {m['ca']:8.1f} {m['usd']:8.3f} | "
              f"{m['act']:8.1f} {m['wri']:8.1f} {m['oth']:8.1f} | {m['inj']:6.2f} "
              f"{srcs.most_common(1)[0][0]:>16} "
              + (f"{st.mean(hits):5.3f}" if hits else "   --") + f" {len(reps):5d}")
        modes = collections.Counter(rows[k]["mode"] for _, rows in reps for k in keys if rows[k]["mode"])
        if modes:
            tot = sum(modes.values())
            print(f"{'  note_write_mode':<30}| " + "  ".join(f"{k2}={v/tot:.2%}" for k2, v in modes.most_common()))
            reasons = collections.Counter(rows[k]["reason"] for _, rows in reps for k in keys if rows[k]["reason"])
            print(f"{'  note_write_reason':<30}| " + "  ".join(f"{k2}={v}" for k2, v in reasons.most_common()))
    print("\n口径:order in {2,3} 的后继槽;raw=Σ(prompt+completion);cache=Σ(uncached_prompt+completion);"
          "USD=prices.json 折算价(非实际支出);act/wri/oth = actor / 写便签 / 抽取+检索+判官;"
          "InjK = 注入载荷 token/1000;hit = 便签轮 cached/prompt。")
    print("估计值:cached_src != usage 的调用走最长公共前缀估计器,论文里须带星号并报估计占比。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
