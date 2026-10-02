#!/usr/bin/env python3
"""按重放(replay)聚合的 Table-3 口径全臂对比。

与 exam_table3.py 同口径(order in {2,3}、Tok(K)=轨迹字符/4000、Inj(K)=注入字符/4000、
linked 旗标 task_link_flags_v2、共有槽 = 所有纳入臂的交集),区别只有两点:

  1. 臂名写**基名**(如 mem_v5knote_v5base),脚本自动收集同基名的全部重放
     mem_v5knote_v5base / _r2 / _r3 ...(正则 ^<base>(_r\\d+)?$);
  2. 每臂先**逐槽对重放取均值**(resolve 取 0/1 均值)再算层级统计;并多打一行
     "±sd" = **重放间** SD:把每次重放单独算出的层级均值取 SD(n=重放数);
     n=2 时 SD 无定义,报 range/2 并在表尾标注。

用法: exam_table3_reps.py [--frame test|train] [--min-cov 0.95] <臂基名 ...>
  臂基名可写 "run:base" 从别的 run 取(如 v3main100:seq)。
  覆盖 = 该重放臂的槽数 / 全部候选重放臂的最大槽数;低于 --min-cov 的重放被丢弃(会打印)。
"""
import json, re, sys, statistics as st, pathlib

_argv = sys.argv[1:]
FRAME = "test"
if "--frame" in _argv:
    i = _argv.index("--frame"); FRAME = _argv[i + 1]; del _argv[i:i + 2]
MIN_COV = 0.95
if "--min-cov" in _argv:
    i = _argv.index("--min-cov"); MIN_COV = float(_argv[i + 1]); del _argv[i:i + 2]

# 复用 exam_table3.py 的加载函数与口径常量(不改动该文件)。
sys.argv = [sys.argv[0], "--frame", FRAME]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exam_table3 as T3  # noqa: E402
sys.argv = [sys.argv[0]] + _argv

R, LABEL, DEFRUN, _CH = T3.R, T3.LABEL, T3.DEFRUN, T3._CH


def discover(run, base):
    """返回 [(臂名, rows)],臂名匹配 ^base(_r\\d+)?$ 且 results 非空。"""
    pat = re.compile(r"^" + re.escape(base) + r"(_r\d+)?$")
    names = set()
    for d in (T3.RUNS / f"{run}").glob(f"*__{base}*"):
        nm = d.name.rsplit("__", 1)[1] if "__" in d.name else ""
        if pat.match(nm): names.add(nm)
    out = []
    for nm in sorted(names, key=lambda s: (len(s), s)):
        rows = T3.load(run, nm)
        if rows: out.append((nm, rows))
    return out


def cell(vals_res, vals_steps, vals_tok):
    """与 exam_table3.cell 同格式,但吃的是已经算好的逐槽值列表。"""
    if not vals_res: return f"{'--':>5}", f"{'--':>11}", f"{'--':>11}"
    res = f"{100*st.mean(vals_res):5.1f}"
    stp = f"{st.mean(vals_steps):5.1f}({st.median(vals_steps):4.0f})"
    tok = f"{st.mean(vals_tok):5.1f}({st.median(vals_tok):4.1f})" if vals_tok else "     --     "
    return res, stp, tok


def strat_stats(rows, keys, sel):
    """一个层级的六个标量: res%, steps_mean, steps_med, tok_mean, tok_med, 以及 keys 数。"""
    ks = [k for k in keys if sel(rows[k])]
    if not ks: return None
    s = [rows[k]["steps"] for k in ks]
    t = [rows[k]["ktok"] for k in ks if rows[k]["ktok"]]
    return dict(res=100 * st.mean(rows[k]["res"] for k in ks),
                sm=st.mean(s), smed=st.median(s),
                tm=(st.mean(t) if t else None), tmed=(st.median(t) if t else None))


def spread(vals):
    """重放间离散度: n>=3 用样本 SD, n==2 用 range/2, n<2 无。"""
    v = [x for x in vals if x is not None]
    if len(v) < 2: return None
    return (max(v) - min(v)) / 2 if len(v) == 2 else st.stdev(v)


SEL = [("Linked", lambda r: r["linked"]), ("Unlinked", lambda r: not r["linked"]), ("Overall", lambda r: True)]


def main():
    want = _argv
    if not want:
        print("用法: exam_table3_reps.py [--frame test|train] [--min-cov F] <臂基名 ...>", file=sys.stderr)
        return 2
    # 1) 收集每个基名下的全部重放
    groups = []          # [(key, [(armname, rows), ...])]
    for a in want:
        run, _, base = a.rpartition(":")
        reps = discover(run or DEFRUN, base)
        if not reps:
            print(f"(无数据 {a})", file=sys.stderr); continue
        # 2026-09-15:显示键**不能**把 memory 系统名一律吃掉。原式对
        # mem_v5knote_a27mems12 与 mem_v5shuf_a27mems12 会算出同一个键 a27mems12,
        # 两条臂被合并成一行、重放被混在一起 —— 那正好把"打乱便签对照"这个处理抹掉。
        # 只对 v5shuf 例外(负向先行断言),其余臂的键**逐字不变**,旧表的行名不受影响。
        key = "seq" if base == "seq" else re.sub(r"^mem_(?!v5shuf_)[A-Za-z0-9]+_", "", base)
        groups.append((key, reps))
    if not groups: return 1
    # 2) 覆盖过滤: 分母 = 全部候选重放臂的最大槽数
    denom = max(len(r) for _, reps in groups for _, r in reps)
    kept, dropped = [], []
    for key, reps in groups:
        ok = [(nm, r) for nm, r in reps if len(r) / denom >= MIN_COV]
        dropped += [(nm, len(r)) for nm, r in reps if len(r) / denom < MIN_COV]
        if ok: kept.append((key, ok))
        else: print(f"(基名 {key} 的重放全部低于覆盖阈值,已丢弃)", file=sys.stderr)
    if not kept: return 1
    # 3) 共有槽 = 所有纳入重放臂的交集
    keys = sorted(set.intersection(*[set(r) for _, reps in kept for _, r in reps]))
    first = kept[0][1][0][1]
    nL = sum(1 for k in keys if first[k]["linked"])
    print(f"{_CH.replace('.txt','')} 共有槽 {len(keys)}(linked {nL} / unlinked {len(keys)-nL});"
          f"臂基名 {len(kept)} 个;重放共 {sum(len(r) for _, r in kept)} 条;覆盖分母 {denom} 槽,阈值 {MIN_COV:g}")
    print("各臂重放: " + "  ".join(f"{k}[" + ",".join(f"{nm.split('__')[-1]}:{len(r)}" for nm, r in reps) + "]"
                                  for k, reps in kept))
    if dropped: print("覆盖不足被丢弃: " + " ".join(f"{nm}:{n}" for nm, n in dropped))
    print()
    hdr = f"{'Memory layer':<34}| {'Linked':^26}| {'Unlinked':^26}| {'Overall':^26}| Inj(K) n_rep"
    print(hdr)
    print(f"{'':<34}| " + " | ".join("  ".join(f"{x:^8}" for x in ("Res%", "Steps", "Tok(K)")) for _ in range(3)) + " |")
    print("-" * 140)
    n2 = False
    for key, reps in kept:
        nrep = len(reps)
        # --- 均值行: 逐槽对重放取均值,再算层级统计 ---
        avg = {}
        for k in keys:
            rs = [r[k] for _, r in reps]
            tk = [x["ktok"] for x in rs if x["ktok"]]
            avg[k] = dict(res=st.mean(1 if x["res"] else 0 for x in rs), steps=st.mean(x["steps"] for x in rs),
                          ktok=(st.mean(tk) if tk else None), inj=st.mean(x["inj"] for x in rs),
                          linked=rs[0]["linked"])
        cells = []
        for _, sel in SEL:
            ks = [k for k in keys if sel(avg[k])]
            cells.append(cell([avg[k]["res"] for k in ks], [avg[k]["steps"] for k in ks],
                              [avg[k]["ktok"] for k in ks if avg[k]["ktok"]]))
        inj = st.mean(avg[k]["inj"] for k in keys)
        L, U, O = cells
        print(f"{LABEL.get(key, key):<34}|{L[0]} {L[1]} {L[2]} |{U[0]} {U[1]} {U[2]} |{O[0]} {O[1]} {O[2]} | {inj:5.2f} {nrep:5d}")
        # --- ±sd 行: 每次重放单独算层级均值, 再取重放间离散度 ---
        per = [{name: strat_stats(r, keys, sel) for name, sel in SEL} for _, r in reps]
        injs = [st.mean(r[k]["inj"] for k in keys) for _, r in reps]
        sd_cells = []
        for name, _ in SEL:
            vs = [p[name] for p in per]
            if any(v is None for v in vs): sd_cells.append((f"{'--':>5}", f"{'--':>11}", f"{'--':>11}")); continue
            d = {f: spread([v[f] for v in vs]) for f in ("res", "sm", "smed", "tm", "tmed")}
            fr = f"{d['res']:5.1f}" if d["res"] is not None else f"{'--':>5}"
            fs = f"{d['sm']:5.1f}({d['smed']:4.1f})" if d["sm"] is not None else f"{'--':>11}"
            ft = f"{d['tm']:5.1f}({d['tmed']:4.1f})" if d["tm"] is not None else f"{'--':>11}"
            sd_cells.append((fr, fs, ft))
        si = spread(injs)
        L, U, O = sd_cells
        tag = "  ±sd" + ("(range/2)" if nrep == 2 else "")
        n2 = n2 or nrep == 2
        print(f"{tag:<34}|{L[0]} {L[1]} {L[2]} |{U[0]} {U[1]} {U[2]} |{O[0]} {O[1]} {O[2]} | "
              + (f"{si:5.2f}" if si is not None else "   --") + f" {'':5}")
    print("\n口径:order in {2,3} 的后继槽;Tok(K)=轨迹字符/4000;Inj(K)=注入字符/4000;linked 旗标 task_link_flags_v2。")
    print("均值行 = 先逐槽对重放取均值(resolve 为 0/1 均值)再做层级统计;±sd 行 = 每次重放单独算出的层级均值之间的离散度,n=n_rep。")
    if n2: print("注意:n_rep=2 的臂,SD 无样本意义,表中报的是 range/2(已在行首标注)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
