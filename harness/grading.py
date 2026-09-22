"""判分单一来源(五份旧拷贝的收敛;架构取 chain_batch.ChainJob.grade:
一次性拷贝 + 测试文件重置 + 累积 test_patch 1..k + test_cmds,宿主文件传补丁绕 ARG_MAX)。
吸收审计修复:F1(write-tree 影子快照 diff)、F2(不可判分元数据)、F3(逐文件重置)、
F4(P2P 逐测试核对)、F13(超时捕获 + finally 清理)。"""
import json, pathlib, re, shutil, subprocess, time

from . import constants as K
from .env import RepoEnv

FILE_RE = re.compile(r"^diff --git a/(\S+)", re.M)


# ---------- 通用工具 ----------

def sh(cmd, timeout=900):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def wait_endpoint(ep_file, max_minutes=300):
    """RSM_API_BASE 设置时直连绕过;否则轮询 endpoint 文件的 /health。"""
    import os
    direct = os.environ.get("RSM_API_BASE")
    if direct:
        return direct
    for _ in range(max_minutes * 2):
        try:
            ep = pathlib.Path(ep_file).read_text().strip()
            if ep and sh(f"curl -s -m 3 http://{ep}/health -o /dev/null").returncode == 0:
                return f"http://{ep}/v1"
        except FileNotFoundError:
            pass
        time.sleep(30)
    raise RuntimeError(f"endpoint never became healthy: {ep_file}")


def detect_conda_prefix(sif):
    r = sh(f"apptainer exec {sif} ls /opt/miniconda3/envs 2>/dev/null")
    if "testbed" in r.stdout:
        return "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed 2>/dev/null; "
    return ""


def detect_repo_dir(sif):
    r = sh(f"apptainer exec {sif} bash -c 'ls -d /*/.git 2>/dev/null'", timeout=120)
    dirs = [l.rsplit("/.git", 1)[0] for l in r.stdout.split()]
    return dirs[0] if len(dirs) == 1 else "/testbed"


def patch_file_list(patch):
    return list(dict.fromkeys(FILE_RE.findall(patch or "")))


def apply_patch(env, text, name, three_way=True):
    # 大补丁经宿主文件传递(bind 的 .git/rsm_tmp/ 对 git diff 不可见),绕开 ARG_MAX
    tmp = pathlib.Path(env._host_repo) / ".git/rsm_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / f"{name}.patch").write_text(text)
    cmd = f"git apply --whitespace=nowarn .git/rsm_tmp/{name}.patch"
    if three_way:
        cmd += f" || git apply --3way --whitespace=nowarn .git/rsm_tmp/{name}.patch"
    r = env.raw(cmd)
    return r["returncode"] == 0, r["output"][-K.APPLY_TAIL:]


def parse_pytest(out):
    res = {}
    for line in out.splitlines():
        m = re.match(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)\s+(\S+)", line.strip())
        if m:
            res[m.group(2)] = m.group(1)
    return res


# ---------- F1:任务边界影子快照(不动 HEAD,判分重置语义不受影响) ----------

def tree_snapshot(env):
    """索引 write-tree 快照:返回 tree oid;不产生 commit,HEAD 恒为 base。"""
    return env.raw("git add -A >/dev/null 2>&1; git write-tree; git reset -q")["output"].strip()


def tree_delta(env, prev_tree, cur_tree):
    """本任务真增量 diff(替代前缀切片,F1 根修复)。"""
    if not prev_tree or not cur_tree:
        return ""
    return env.raw(f"git diff {prev_tree} {cur_tree}")["output"]


# ---------- F2:不可判分元数据(F15 修复后仅剩 F2P 为空的理论情形) ----------

def _cmds_str(spec):
    cmds = spec.get("test_cmds") or ""
    return " ".join(cmds) if isinstance(cmds, list) else str(cmds)


def _f2p_p2p(spec):
    f2p = spec.get("FAIL_TO_PASS") or []
    f2p = json.loads(f2p) if isinstance(f2p, str) else f2p
    p2p = spec.get("PASS_TO_PASS") or []
    p2p = json.loads(p2p) if isinstance(p2p, str) else p2p
    return f2p, p2p


def test_command(spec):
    """返回 (命令, 是否合成)。F15:16 个任务的官方 test_cmds 是 `&& true` 占位符
    (上游挖掘残缺),但 F2P/P2P 测试 ID 齐全 —— 按官方 ID 所在文件合成 pytest 命令
    (与 train_rollout 对 SWE-rebench 的成熟做法一致),救活 16 槽/5 条死链。"""
    cmds = _cmds_str(spec)
    if "pytest" in cmds:
        return cmds, False
    f2p, p2p = _f2p_p2p(spec)
    files = sorted({t.split("::")[0] for t in f2p + p2p if t})
    if not files:
        return "", False
    return ("python -m pytest --no-header -rA -p no:cacheprovider "
            + " ".join(files)), True


def is_gradable(spec):
    return bool(test_command(spec)[0])


# ---------- 判分主体 ----------

def grade(host_repo, sif, repo_path, prefix, tasks, k, out_name, run_tag="", base_oid=None,
          only_k=False):
    """官方语义:一次性拷贝里重置测试文件 → 累积打 test_patch 1..k → 跑第 k 题 test_cmds。
    返回统一 schema(键恒在):resolved / gradable / f2p / p2p_failed / p2p_missing /
    collection_errors / error / error_stage。F13:任何异常都收敛为 error 行,不上抛。"""
    rec = {"resolved": False, "gradable": True, "f2p": {}, "p2p_failed": [],
           "p2p_missing": [], "p2p_unscoped": [], "collection_errors": [],
           "error": None, "error_stage": None}
    spec = tasks[k - 1]
    if not is_gradable(spec):
        rec.update(resolved=None, gradable=False)
        return rec
    # 判分拷贝目录带 run_tag+pid,避免同节点不同 run 互相 rmtree(旧坑 8.1)
    gdir = pathlib.Path(f"/tmp/rsm_grade/{run_tag}_{pathlib.Path(out_name).name}_{k}_{time.time_ns() % 10**9}")
    try:
        shutil.copytree(host_repo, gdir, symlinks=True)
        genv = RepoEnv(image=str(sif), host_repo=str(gdir), repo_path=repo_path, prefix=prefix)
        # F3:逐文件重置到链首 base(commit 协议下 HEAD 含前题改动,不能作测试重置锚)
        ref = base_oid or "HEAD"
        # only_k(SCB 等每题独立 base 的语料):两题是相隔数十天的两个独立实例,
        # 前题 test_patch 在后题的树上根本不适用,累积打必然失败。此时只处理第 k 题。
        tscope = [tasks[k - 1]] if only_k else tasks[:k]
        tfiles = []
        for b in tscope:
            tfiles += patch_file_list(b["test_patch"])
        for f in sorted(set(tfiles)):
            genv.raw(f"if git cat-file -e {ref}:'{f}' 2>/dev/null; then git checkout -q {ref} -- '{f}'; else rm -f '{f}'; fi")
        # 累积应用 test_patch 1..k(3way 容错)。F14:空 test_patch = 该任务无测试变更
        # (64/304 任务如此,F2P 测试已在仓库里),跳过而非报错——旧管线把它当 apply 失败,
        # 全臂堵死 81 个 pos>=2 判分槽位(L38/U43)。
        for b in tscope:
            if not (b.get("test_patch") or "").strip():
                continue
            ok, err = apply_patch(genv, b["test_patch"], f"tp{b['order']}")
            if not ok:
                rec.update(error=f"test_patch {b['order']} apply failed: {err}", error_stage="apply")
                return rec
        cmd, synth = test_command(spec)
        rec["synth_cmds"] = synth   # F15 透明度:该槽判分命令为官方 ID 合成
        r = genv.raw(cmd, timeout=K.GRADE_TIMEOUT)
        res = parse_pytest(r["output"])
        if not res:
            rec.update(error=f"no tests parsed (pytest rc={r['returncode']})", error_stage="no_tests_parsed")
            return rec
        # 文件级 ERROR 行(无 '::')= 收集错误,该文件的测试全部蒸发(F4 的钥匙)
        rec["collection_errors"] = [t for t, v in res.items() if v == "ERROR" and "::" not in t]
        f2p = spec["FAIL_TO_PASS"]
        f2p = json.loads(f2p) if isinstance(f2p, str) else f2p
        p2p = spec.get("PASS_TO_PASS") or []
        p2p = json.loads(p2p) if isinstance(p2p, str) else p2p
        rec["f2p"] = {t: res.get(t, "MISSING") for t in f2p}
        # F4:P2P 蒸发判定只认"收集错误文件"(import 崩了整文件测试蒸发=假阳性温床,判失败)。
        # 文件正常出结果但某 P2P 未出现 = test_cmds 选择性执行(逐测试点名很常见),归 unscoped
        # 不计入 —— 首链实测曾把 130 个未点名 P2P 误判失踪,过严错杀 resolve(2026-08-27 修)。
        cerr = set(rec["collection_errors"])
        rec["p2p_missing"] = [t for t in p2p if t not in res and t.split("::")[0] in cerr]
        rec["p2p_unscoped"] = [t for t in p2p if t not in res and t.split("::")[0] not in cerr]
        rec["p2p_failed"] = [t for t in p2p if res.get(t) not in (None, "PASSED")]
        rec["resolved"] = (bool(rec["f2p"])
                           and all(v == "PASSED" for v in rec["f2p"].values())
                           and not rec["p2p_failed"] and not rec["p2p_missing"])
        return rec
    except subprocess.TimeoutExpired:
        rec.update(error=f"grade timeout >{K.GRADE_TIMEOUT}s", error_stage="timeout")
        return rec
    except Exception as e:
        # 判分基建故障(如 /tmp 满)标 crashed:阻止链固化,留待重跑(P2-6);超时除外(确定性慢,计入完成)
        rec.update(error=f"{type(e).__name__}: {str(e)[:200]}", error_stage="grade_infra", crashed=True)
        return rec
    finally:
        shutil.rmtree(gdir, ignore_errors=True)
