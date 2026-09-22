#!/usr/bin/env python3
"""build_shuf_pool.py — 给**打乱便签对照臂**(mem_v5shuf_*)造便签池(2026-09-15)。

从一次**已经跑完的 v5knote 考场**里,把每条链在每个 order 上**真实注入过**的
`<memory_context>` 正文原样抠出来,存成 {chain_id: {"2": body, "3": body}}。

为什么从 traj 里抠而不是重新写一遍:
  * 那才是"真便签" —— 同一帧、同一个**未训练 Qwen3-4B**写手、同一份 v5_sharp 模板、
    同样的 FIFO 30K 截断,连 `[instance_id]` 的包头都一模一样。
  * 重写一遍要再起一次写手、再花一遍钱,而且拿到的是另一次采样,徒增一个混杂因素。

口径注意:
  * 只从 **r1**(臂名不带 _r2/_r3 后缀)抠,单一来源 -> 池子跨臂跨重放**完全一致**,
    B(真) − B(乱) 的对照里"注入内容"这一项在所有格子上是同一份。
  * order=1 永远没有跨任务注入,池里不会有 "1"。
  * 地板由注入侧的 RSM_NOTE_SHUFFLE_FLOOR 管(默认 200 字符),这里只做非空过滤。

用法:
  python3 build_shuf_pool.py --run ft1_grpo --arm mem_v5knote_v5base \
      --out /gpfs/.../shuf_pool_v5base.json --min-chains 40
"""
import argparse, json, pathlib, re, sys

HEAD = "<memory_context>\nNotes from earlier work sessions on this repository:\n"
TAIL = "\n</memory_context>"


def body_of(traj_path):
    try:
        o = json.loads(pathlib.Path(traj_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    for m in o.get("messages") or []:
        if m.get("role") != "user":
            continue
        t = m.get("content") or ""
        i = t.find(HEAD)
        if i < 0:
            return None                      # 第一条 user 就没有注入 -> 这题本来就没注入
        j = t.find(TAIL, i)
        if j < 0:
            return None
        return t[i + len(HEAD):j]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", default="/gpfs/radev/pi/dijk/pl662/swe_explore/"
                                           "repo-state-memory/chainswe/runs")
    ap.add_argument("--run", required=True)
    ap.add_argument("--arm", required=True, help="臂目录名,如 mem_v5knote_v5base(不带 _r2/_r3)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-chains", type=int, default=40,
                    help="低于这个链数就**硬失败**,绝不产一个半空的池")
    a = ap.parse_args()

    root = pathlib.Path(a.runs_root) / a.run
    dirs = sorted(d for d in root.glob(f"*__{a.arm}") if d.is_dir())
    pool, n_notes = {}, 0
    for d in dirs:
        chain = d.name.split("__")[0]
        got = {}
        for f in sorted(d.glob("traj_*.json")):
            m = re.match(r"traj_(\d+)_", f.name)
            if not m:
                continue
            k = int(m.group(1))
            if k < 2:
                continue
            b = body_of(f)
            if b and b.strip():
                got[str(k)] = b
                n_notes += 1
        if got:
            pool[chain] = got
    if len(pool) < a.min_chains:
        print(f"!! 池只有 {len(pool)} 条链 < --min-chains {a.min_chains}"
              f"(扫了 {len(dirs)} 个臂目录 {root}/*__{a.arm})", file=sys.stderr)
        return 2
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    lens = sorted(len(v) for ch in pool.values() for v in ch.values())
    print(f"池写好: {out}  链 {len(pool)} 条 / 便签 {n_notes} 条 "
          f"(字符 min {lens[0]} / 中位 {lens[len(lens)//2]} / max {lens[-1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
