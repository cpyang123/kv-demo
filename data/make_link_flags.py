#!/usr/bin/env python3
"""依赖旗标生成器(补档,review 清单 A):按 (链,位置) 计算 pos>=2 任务的 linked 判据——
该任务 gold patch 或 test patch 触碰的文件,与同链更早任务 gold patch 改过的文件有交集。
纯金标材料确定性计算,与运行结果无关。输出 task_link_flags_v2.json(键 "chain|pos"),
并与冻结版 task_link_flags.json(instance 键,08-22)对账。"""
import json, pathlib, re

ROOT = pathlib.Path(__file__).parent  # chainswe/data/
FILE_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.M)


def patch_files(txt):
    return {m.group(2) for m in FILE_RE.finditer(txt or "")}


def main():
    rows = [json.loads(l) for l in open(ROOT / "chainswe_data.jsonl")]
    q = set(l.strip() for l in open(ROOT / "chains/q38_chains.txt"))
    v2 = {}
    for r in rows:
        if r["continuous_id"] not in q:
            continue
        earlier = set()
        for pos, b in enumerate(r["bug_fixes"], 1):
            gold = patch_files(b.get("patch"))
            if pos >= 2:
                touch = gold | patch_files(b.get("test_patch"))
                v2[f"{r['continuous_id']}|{pos}"] = bool(touch & earlier)
            earlier |= gold
    out = ROOT / "task_link_flags_v2.json"
    out.write_text(json.dumps(v2, indent=0))
    L = sum(1 for v in v2.values() if v)
    print(f"v2: {len(v2)} 槽位, linked {L} / unlinked {len(v2)-L}")
    # 对账
    v1 = json.load(open(ROOT.parent.parent / "w1/task_link_flags.json"))
    agree = dis = nocov = 0
    for r in rows:
        if r["continuous_id"] not in q:
            continue
        for pos, b in enumerate(r["bug_fixes"], 1):
            if pos < 2:
                continue
            iid = b["swebench_instance_id"]
            if iid not in v1:
                nocov += 1; continue
            key = f"{r['continuous_id']}|{pos}"
            if v1[iid] == v2[key]:
                agree += 1
            else:
                dis += 1
                print(f"  分歧: {key} ({iid}): v1={v1[iid]} v2={v2[key]}")
    print(f"对账: 一致 {agree}, 分歧 {dis}, v1 未覆盖 {nocov}")


if __name__ == "__main__":
    main()
