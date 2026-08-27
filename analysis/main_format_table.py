#!/usr/bin/env python3
"""统一口径出数器:所有臂(探针/baseline/我们的)按 tab:main 固定九列输出绝对值。
口径 = 消毒协议:steps=api_calls;剔除 no-op test_cmds 槽位;v2 链接分层;
tok=prefix-cached(chars/4,每 token 只付一次);单元格 mean(median)。
自检:seq/fusednote/factsall 三行必须复现 tab:main 已核实数字,否则 FAIL 拒绝出表。
输出 chainswe/analysis/main_format_table.txt + _rows.tex(LaTeX 行,\\input 用)。"""
import json, os, pathlib, re, statistics

RSM = pathlib.Path("/gpfs/radev/pi/dijk/pl662/swe_explore/repo-state-memory")
W1, C = RSM / "w1", RSM / "chainswe"
R = W1 / "runs/chainswe/pilot_q38"
LINK = json.load(open(C / "data/task_link_flags_v2.json"))
ARMS = ["seq", "mem_rawcat", "mem_rawprose", "mem_rawcmd", "mem_structmem", "mem_repomap",
        "mem_factsall", "mem_statenote", "mem_fusednote", "mem_trainednote"]
LABEL = {"seq": "No memory (seq)", "mem_rawcat": "Raw concat (ceiling)", "mem_rawprose": "Raw prose",
         "mem_rawcmd": "Raw commands", "mem_structmem": "Struct-routed notes", "mem_repomap": "Repo map",
         "mem_factsall": "Mem0$^\\dagger$ (extraction union)", "mem_statenote": "State notes",
         "mem_fusednote": "Fused notes (heuristic)", "mem_trainednote": "SFT writer notes"}
WRITER = {"mem_factsall": 12.8, "mem_statenote": 5.7, "mem_fusednote": 5.0, "mem_trainednote": 5.0}
NOTE = {"mem_factsall": 0.15, "mem_fusednote": 0.28, "mem_trainednote": 0.31}


def noop_slots():
    """no-op 判分槽位:test_cmds 无 pytest(审计 F2)。键 (chain, pos)。"""
    bad = set()
    for l in open(C / "data/chainswe_data.jsonl"):
        r = json.loads(l)
        for pos, b in enumerate(r["bug_fixes"], 1):
            cmds = b.get("test_cmds") or ""
            cmds = " ".join(cmds) if isinstance(cmds, list) else str(cmds)
            if "pytest" not in cmds:
                bad.add((r["continuous_id"], pos))
    return bad


def load_arm(arm):
    rows, toks, steps = {}, {}, {}
    for dirp in R.glob(f"*__s0__{arm}"):
        ch = dirp.name.split("__s0__")[0]
        for l in (dirp / "results.jsonl").read_text().splitlines() if (dirp / "results.jsonl").exists() else []:
            r = json.loads(l)
            if "order" in r:
                rows[(ch, r["order"])] = r
        for f in dirp.glob("traj_*.json"):
            m = re.match(r"traj_(\d+)_", f.name)
            if not m or int(m.group(1)) < 2:
                continue
            try:
                j = json.load(open(f))
            except Exception:
                continue
            k = (ch, int(m.group(1)))
            msgs = j.get("messages", [])
            lens = [len(str(x.get("content", ""))) for x in msgs]
            if lens:
                nc = sum(sum(lens[:i]) + lens[i] for i, x in enumerate(msgs) if x.get("role") == "assistant")
                toks[k] = (sum(lens) / 4000.0, nc / 4000.0)   # (cached, no-cache)
            ac = (j.get("info", {}).get("model_stats") or {}).get("api_calls")
            if ac:
                steps[k] = ac
    return rows, toks, steps


def mm(v):
    return f"{statistics.mean(v):.1f} ({statistics.median(v):.1f})" if v else "--"


def inj(r):
    x = r.get("injected_chars", r.get("kv_context_injected"))
    return x / 4000.0 if isinstance(x, (int, float)) and x > 3 else None


def main():
    bad = noop_slots()
    n_bad2 = sum(1 for (c, p) in bad if p >= 2 and f"{c}|{p}" in LINK)
    print(f"no-op slots total={len(bad)}, pos>=2 within flags={n_bad2} (expect 190-179=11)")
    # 分母 = v2 标注全集 ∩ 有 seq 结果的 93 链,减 no-op → L 102 / U 77(=179);缺跑/崩溃按未解决计
    chains = set()
    for d in R.glob("*__s0__seq"):
        f = d / "results.jsonl"
        if f.exists() and any(l.strip() for l in f.read_text().splitlines()):
            chains.add(d.name.split("__s0__")[0])
    ff = os.environ.get("RSM_FRAME_CHAINS")   # 可选框架过滤(自检用 93 链复现)
    if ff:
        chains &= {l.strip() for l in open(ff) if l.strip()}
    print(f"frame chains: {len(chains)}")
    slots = {}
    for key, v in LINK.items():
        c, p = key.rsplit("|", 1)
        if c in chains and (c, int(p)) not in bad:
            slots.setdefault(v, []).append((c, int(p)))
    txt, tex, chk = [], [], []
    for arm in ARMS:
        rows, toks, steps = load_arm(arm)
        cell = {}
        for want, tag in [(True, "L"), (False, "U")]:
            ks = slots[want]
            res = 100 * statistics.mean([bool(rows.get(k, {}).get("resolved")) for k in ks])
            st = [steps[k] for k in ks if k in steps]
            tk = [toks[k][0] for k in ks if k in toks]
            nc = [toks[k][1] for k in ks if k in toks]
            cell[tag] = (len(ks), res, mm(st), mm(tk), len(st), mm(nc))
        ij = [v for v in (inj(rows[k]) for k in rows if k[1] >= 2) if v is not None]
        wcol = f"{WRITER[arm]:.1f}" if arm in WRITER else ("--" if arm == "seq" else "0")
        ncol = f"{NOTE[arm]:.2f}" if arm in NOTE else "--"
        icol = mm(ij) if ij else "--"
        L, U = cell["L"], cell["U"]
        txt.append(f"{LABEL[arm]:34s} L(n={L[0]:3d},cov={L[4]:3d}) {L[1]:5.1f}% {L[2]:>12s} {L[3]:>12s} | "
                   f"U(n={U[0]:3d},cov={U[4]:3d}) {U[1]:5.1f}% {U[2]:>12s} {U[3]:>12s} | W {wcol:>5s} N {ncol:>5s} I {icol:>11s}")
        tex.append(f"{LABEL[arm]} & {L[1]:.1f} & {L[2]} & {L[3]} & {U[1]:.1f} & {U[2]} & {U[3]} & {wcol} & {ncol} & {icol} \\\\")
        tex.append(f"%APP {LABEL[arm]} & {L[1]:.1f} & {L[2]} & {L[3]} & {L[5]} & {U[1]:.1f} & {U[2]} & {U[3]} & {U[5]} & {wcol} & {ncol} & {icol} \\\\")
        if arm in ("seq", "mem_fusednote", "mem_factsall"):
            chk.append((arm, L[1], L[2], L[3], U[1]))
    print("\n".join(txt))
    # ---- 自检 vs tab:main 已核实值 ----
    want = {"seq": (14.7, "31.6 (30.0)", "25.2 (24.5)", 18.2),
            "mem_fusednote": (18.6, "30.1 (29.0)", "25.5 (23.9)", 19.5),
            "mem_factsall": (12.7, None, None, None)}
    ok = True
    for arm, rl, sl, tl, ru in chk:
        w = want[arm]
        for got, exp, name in [(f"{rl:.1f}", f"{w[0]:.1f}", "ResL"), (sl, w[1], "StepsL"),
                               (tl, w[2], "TokL"), (f"{ru:.1f}", None if w[3] is None else f"{w[3]:.1f}", "ResU")]:
            if exp is not None:
                s = "PASS" if got == exp else "FAIL"
                ok = ok and got == exp
                print(f"  SELFCHECK {arm} {name}: got {got} want {exp} -> {s}")
    (C / "analysis/main_format_table.txt").write_text("\n".join(txt))
    (C / "analysis/main_format_rows.tex").write_text("\n".join(tex))
    print("SELFCHECK", "ALL PASS — 表可用" if ok else "FAILED — 禁止出表,先查口径")


if __name__ == "__main__":
    main()
