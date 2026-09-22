"""ChainSWE 统一入口:seq/oracle/indep 基线与全部 memory 臂共用一条主循环。
吸收审计修复:F5(commit 守卫)/F6(steps=api_calls)/F7(崩溃行标记)/F8(原子写+JSON 校验)/
F9(基建异常中止链,不固化)/F10(注入度量分账)/F11(锁心跳+原子接管)/F12(锁下沉公共层)。
用法:
  python -m harness.runner --arm seq --chains-file data/chains/q38_chains_s0.txt --run-id v2pilot
  python -m harness.runner --memory-system fusednote --chains-file ... --run-id v2pilot
"""
import argparse, json, os, pathlib, shutil, sys, threading, time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))   # chainswe/ 为包根

from harness import config as C, constants as K, grading as G          # noqa: E402
from harness.env import RepoEnv                                         # noqa: E402
from minisweagent.agents.default import DefaultAgent                    # noqa: E402
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel  # noqa: E402
import memory                                                           # noqa: E402 (import 即注册全部插件)
from memory import base as mem_base                                     # noqa: E402

# 2026-09-22 精确 token 台账(DESIGN_inline_ledger_0921.md §B):**默认关**。
# 不设 RSM_LEDGER=1 时 _LEDGER 恒为 None,下面每一处钩子都是 `if _LEDGER:` 的假分支,
# 模块甚至不被 import —— 旧臂的请求字节/采样/控制流/输出字段逐字不变(keep-original A/B)。
_LEDGER = None
if os.environ.get("RSM_LEDGER") == "1":
    from harness import ledger as _LEDGER                                   # noqa: E402

INFRA_EXC = ("Timeout", "Connection", "ServiceUnavailable", "InternalServer",
             "APIError", "RateLimit", "BadGateway")   # 真基建故障:中止链留待重跑
CONFIG_EXC = ("Authentication", "NotFound", "PermissionDenied")   # 配置错:FATAL 杀整个 lane,人必须看见(P0-2b)
CTX_PHRASES = ("context length", "longer than the maximum model length", "too long",
               "maximum context")   # 裸 BadRequest 版上下文溢出的报文特征(P1-3)
TASK_ERRORS = ("ContextWindow", "harness_commit_failed")   # 确定性任务级失败:计入完成,不触发重跑


class ChainAbort(Exception):
    """基建故障:中止本链、不写满 results(F9:留待重跑而非固化为已完成失败链)。"""


class CommitFailed(Exception):
    """harness commit 确定性失败(agent 毁 repo 等):补满失败行收链,不走重跑(P0-2a)。"""


# ---------- 认领锁(F11/F12):所有臂共用 ----------

def results_complete(rf, n_tasks):
    """完成判定 = 行数达标 + 逐行 JSON 有效含 order + 无未定性崩溃行(F8+P1)。
    crashed 行只有确定性任务级失败(TASK_ERRORS)才算数——auth 错/基建崩不固化,留待重跑。"""
    if not rf.exists():
        return False
    lines = [l for l in rf.read_text().splitlines() if l.strip()]
    if len(lines) != n_tasks:
        return False
    try:
        for l in lines:
            r = json.loads(l)
            if "order" not in r:
                return False
            if r.get("crashed") and not any(t in str(r.get("exit", "")) for t in TASK_ERRORS):
                return False
            if r.get("mem_write_error"):   # F17:记忆臂写侧失败=链前提破坏,不固化
                return False
        return True
    except Exception:
        return False


def claim_chain(out):
    """返回 True=拿到锁。陈旧接管走 os.rename 原子夺取,rename 赢者才继续。"""
    out.mkdir(parents=True, exist_ok=True)
    claim = out / ".claim"
    ident = f"{os.environ.get('SLURM_JOB_ID', '?')} {os.getpid()} {time.time()}"
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, ident.encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            if time.time() - claim.stat().st_mtime < K.CLAIM_STALE_SECS:
                return False
            stale = claim.with_name(f".claim.stale.{os.getpid()}")
            os.rename(claim, stale)   # 原子夺取
            stale.unlink(missing_ok=True)
        except (FileNotFoundError, OSError):
            return False
        try:
            fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ident.encode())
            os.close(fd)
            return True
        except FileExistsError:
            return False


class Heartbeat:
    def __init__(self, claim):
        self.claim, self._stop = claim, threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while not self._stop.wait(K.CLAIM_HEARTBEAT):
            try:
                if self.claim.exists():   # touch 会重建已删文件;完成路径 unlink 后不复活
                    self.claim.touch()
            except OSError:
                pass

    def stop(self):
        self._stop.set()


def atomic_results(out, recs):
    """F8:tmp + os.replace 原子写,行尾带 \\n。"""
    tmp = out / "results.jsonl.tmp"
    tmp.write_text("".join(json.dumps(r) + "\n" for r in recs))
    os.replace(tmp, out / "results.jsonl")


# ---------- 链执行 ----------

class ChainRunner:
    def __init__(self, chain, arm, mode, out, model_name, model_kwargs, step_limit, run_id):
        self.c, self.arm, self.mode, self.out = chain, arm, mode, out
        self.model_name, self.model_kwargs, self.step_limit = model_name, model_kwargs, step_limit
        self.tasks = sorted(chain["bug_fixes"], key=lambda b: b["order"])
        self.sif = C.SIF_DIR / (chain["docker_image"].split(":")[1] + ".sif")
        # /tmp 工作区带 run_id+pid:同节点不同 run/lane 不互相 rmtree(旧坑)
        self.host_root = pathlib.Path(f"/tmp/rsm_chain/{run_id}_{out.name}_{os.getpid()}")
        if self.host_root.exists():
            shutil.rmtree(self.host_root)
        self.host = self.host_root / "repo"
        self.repo_path = G.detect_repo_dir(self.sif)
        self.prefix = G.detect_conda_prefix(self.sif)
        self.env = RepoEnv(image=str(self.sif), host_repo=str(self.host),
                           repo_path=self.repo_path, prefix=self.prefix)
        self.mem = mem_base.REGISTRY[mode]() if mode in mem_base.REGISTRY else None
        # 2026-09-15:把链号挂到 layer 上。打乱便签臂(v5shuf)要按链做固定 seed 的错位映射,
        # 而 layer 的构造签名是零参的(base.py 的接入合同),不改合同就只能挂属性。
        # 对**所有既有臂是惰性的**:没有任何一个旧 layer 读这两个属性。
        if self.mem is not None:
            self.mem.chain_id = chain.get("continuous_id") or out.name.split("__")[0]
            self.mem.task_idx = 0
            # 2026-09-17:把本链 out 目录也挂上(rbankj 的写手调用台账 writer_calls.jsonl 落在这)。
            # 同样对**所有既有臂惰性**:没有任何一个旧 layer 读这个属性。
            self.mem.out_dir = out
        self.run_tag = run_id
        # 2026-09-06:每题独立 base 的语料(SCB)。链上带 per_task_commit=true 时,
        # 每题先 reset 到本题自己的 commit_hash —— 因为两题是同仓库的两个独立上游实例,
        # 后题的 base_commit 在真实历史上已经包含前题的修复,不该继承前题的工作区改动。
        # ChainSWE 的链不带这个键,走原路径,行为逐字不变。
        self.per_task = bool(chain.get("per_task_commit"))
        self.task_oid = None
        if _LEDGER: _LEDGER.install()

    # -- 基线臂工作区协议 --
    def reset_workspace(self):
        # 回 base(官方 oracle/indep 语义);reset --hard 顺带清 unmerged 条目(P2)
        r = self.env.raw(f"git reset -q --hard {self.base_oid} && git clean -fdq")
        if r["returncode"] != 0:
            raise ChainAbort(f"reset_workspace failed: {r['output'][-200:]}")

    def apply_golds(self, upto):
        for b in self.tasks[:upto]:
            ok, err = G.apply_patch(self.env, b["patch"], f"gold{b['order']}")
            if not ok:
                return False, err
        return True, ""

    # -- F5:commit 守卫(agent 禁 commit;harness 每题一 commit 是官方对齐协议,见 _commit_task) --
    def _head(self):
        import re as _re
        m = _re.findall(r"\b[0-9a-f]{40}\b", self.env.raw("git rev-parse HEAD")["output"])
        return m[-1] if m else ""

    def _guard_commits(self, task_base, rec):
        head = self._head()
        # 两侧都必须是合法 oid 才比对,防 rev-parse 报错文本触发伪 reset
        if len(head) == 40 and len(task_base) == 40 and head != task_base:
            rec["agent_committed"] = True
            self.env.raw(f"git reset --soft {task_base}")
        return rec

    def _commit_task(self, idx):
        """官方 ChainSWE Seq 对齐:每题结束由 harness 提交本题改动(agent 仍禁 commit)。
        失败先自救(锁文件/权限)重试一次;仍败=agent 毁 repo 的确定性结局,抛 CommitFailed
        由调用方补满失败行收链,不走 ChainAbort 重跑循环(P0-2a)。"""
        cmd = ("git add -A && git -c user.name=rsm -c user.email=rsm@local "
               f"commit -q --allow-empty -m 'rsm: task {idx}'")
        r = self.env.raw(cmd)
        if r["returncode"] != 0:
            self.env.raw("rm -f .git/index.lock; chmod -R u+rw . 2>/dev/null; true")
            r = self.env.raw(cmd)
            if r["returncode"] != 0:
                raise CommitFailed(f"task {idx}: {r['output'][-200:]}")
        return self._head()

    def _material(self, agent, delta):
        mc, tc, fc = K.raw_msg_cap(), K.raw_total_cap(), K.raw_final_cap()
        parts = []
        for m in agent.messages:
            r, c = m.get("role"), m.get("content")
            if r == "assistant" and isinstance(c, str):
                parts.append("ASSISTANT:\n" + c[:mc])
            elif r == "user" and parts and isinstance(c, str):
                parts.append("OBSERVATION:\n" + c[:mc])
        raw = "\n\n".join(parts)
        # FINAL DIFF 锚永不被终截断吃掉(P5):raw 部分先给 diff 段留位,env 覆盖不成套也安全
        head = raw[:min(tc, max(0, fc - K.DIFF_CAP - 16))]
        return (head + "\n\nFINAL DIFF:\n" + delta[:K.DIFF_CAP])[:fc]

    def run(self, ep_file):
        recs = []
        mem_zero_streak = 0
        try:
            self.base_oid = self._head()         # 链首 base:判分/重置的不动锚
            if len(self.base_oid) != 40:
                raise ChainAbort(f"cannot resolve base oid: {self.base_oid[:80]}")
            for idx, spec in enumerate(self.tasks, 1):
                if self.mode == "indep":
                    self.reset_workspace()
                elif self.mode == "oracle":
                    self.reset_workspace()
                    ok, err = self.apply_golds(idx - 1)
                    if not ok:
                        recs.append(self._blank_rec(idx, spec, error="oracle setup: " + err))
                        atomic_results(self.out, recs)
                        self.reset_workspace()   # 清掉 3way 失败残留,别让污染级联到下一题(P2)
                        continue
                if self.per_task:
                    tgt = spec.get("commit_hash") or ""
                    r = self.env.raw(f"git reset -q --hard {tgt} && git clean -fdq")
                    if r["returncode"] != 0:
                        recs.append(self._blank_rec(idx, spec,
                                    error=f"per-task reset to {tgt[:12]} failed: {r['output'][-200:]}"))
                        atomic_results(self.out, recs)
                        continue
                    self.task_oid = self._head()
                task_base = self._head()         # 本题起点(=base+前题 harness commits)
                t_wrap0 = time.time()
                if _LEDGER: _LEDGER.begin_session(run_id=self.run_tag, chain=self.c.get("continuous_id"), slot=idx, instance=spec["swebench_instance_id"], arm=self.arm, out=self.out)
                if self.mem:
                    self.mem.task_idx = idx          # 2026-09-15:见 __init__ 的说明,旧臂不读
                    w = self.mem.wrap(spec["problem_statement"], self.env)
                else:
                    w = mem_base.WrapResult(spec["problem_statement"])
                wrap_secs = round(time.time() - t_wrap0, 1)
                if _LEDGER: _LEDGER.note_injected(w)
                mk = dict(self.model_kwargs)
                if ep_file:
                    mk["api_base"] = G.wait_endpoint(ep_file)
                model = LitellmTextbasedModel(model_name=self.model_name, model_kwargs=mk,
                                              **C.model_template_kwargs())
                agent = DefaultAgent(
                    model, self.env,
                    system_template=C.system_template(self.step_limit),
                    instance_template=C.instance_template(self.repo_path),
                    step_limit=self.step_limit, cost_limit=K.COST_LIMIT,
                    output_path=self.out / f"traj_{idx}_{spec['swebench_instance_id']}.json")
                t0 = time.time()
                err = None
                try:
                    agent.run(task=w.text)
                    exit_s = agent.messages[-1].get("extra", {}).get("exit_status", "?")
                except Exception as e:
                    name = type(e).__name__
                    err = f"{name}: {str(e)[:200]}"
                    if "ContextWindow" in name or any(p in str(e)[:500] for p in CTX_PHRASES):
                        exit_s = "error:ContextWindowExceededError"   # 任务级结局,计入完成(P1-3)
                    elif any(t in name for t in CONFIG_EXC):
                        print(f"FATAL config error, killing lane: {err}", flush=True)
                        raise SystemExit(2)                    # 配置错必须人看见(P0-2b)
                    elif any(t in name for t in INFRA_EXC):
                        raise ChainAbort(err) from e           # F9:基建故障不固化
                    else:
                        exit_s = f"error:{name}"
                # 2026-09-22 会话内自写便签(v5inline):**位置 = 会话末尾、判分之前**,越早发命中率越高;
                # 便签轮不执行任何命令、不经过 agent.query(),n_steps 逐字不变。旧臂 getattr -> False。
                if self.mem is not None and getattr(self.mem, "inline_self_write", False):
                    from harness import inline_note as INL
                    self.mem.pending_inline = INL.run_inline_turn(agent=agent, model=model, exit_status=exit_s, out=self.out, chain=getattr(self.mem, "chain_id", None), order=idx, instance=spec["swebench_instance_id"], arm=self.arm, run_id=self.run_tag)
                rec = {"order": idx, "instance": spec["swebench_instance_id"], "arm": self.arm,
                       "proto": K.PROTO_TAG,
                       "exit": exit_s, "secs": round(time.time() - t0, 1), "wrap_secs": wrap_secs,
                       "n_steps": agent.n_calls,                       # F6:api_calls 口径
                       "n_assistant_msgs": sum(1 for m in agent.messages if m.get("role") == "assistant"),
                       "injected_chars": w.injected_chars, "injected": w.injected,   # F10 分账
                       "map_chars": w.map_chars,
                       "error": err, "crashed": bool(err)}             # F7:崩溃行显式标记
                self._guard_commits(task_base, rec)                    # F5
                # 官方对齐:seq 系(含 memory 臂)每题 harness commit;indep/oracle 每题重置,无需 commit
                try:
                    if self.mode not in ("indep", "oracle"):
                        self._commit_task(idx)
                except CommitFailed as ce:
                    # agent 毁 repo 的确定性结局:本题+余题全记失败行,收链不重跑(P0-2a)
                    rec.update(exit="harness_commit_failed", error=str(ce)[:200], crashed=False,
                               resolved=False, gradable=None, proto=K.PROTO_TAG)
                    recs.append(rec)
                    for j in range(idx + 1, len(self.tasks) + 1):
                        recs.append(self._blank_rec(j, self.tasks[j - 1],
                                                    error=f"chain dead after harness_commit_failed@{idx}"))
                    atomic_results(self.out, recs)
                    print(f"  [{self.c['continuous_id']}|{self.arm}] chain closed: commit failed@{idx}", flush=True)
                    return recs
                g = G.grade(self.host, self.sif, self.repo_path, self.prefix,
                            self.tasks, idx, self.out.name, run_tag=self.run_tag,
                            base_oid=(self.task_oid if self.per_task else self.base_oid),
                            only_k=self.per_task)
                # F7 补:agent 错误不被判分结果的 error=None 覆盖,两侧都有则拼接
                g["error"] = "; ".join(x for x in (rec.get("error"), g.get("error")) if x) or None
                rec.update(g)
                # session 边界:本题真增量 diff = 两个 commit 之差(F1;commit 协议下天然精确)→ 写记忆
                t_add0 = time.time()
                try:
                    delta = self.env.raw(f"git diff {task_base} HEAD")["output"] \
                        if self.mode not in ("indep", "oracle") else ""
                    if self.mem:
                        spec["_rsm_resolved"] = rec.get("resolved")   # RB 臂成败分支桥(grade 先于 write)
                        rec["mem_nodes"] = self.mem.write(spec, self._material(agent, delta),
                                                          delta, self.env)
                        rec["mem_write_secs"] = round(time.time() - t_add0, 1)
                        # 2026-09-17:插件回填结果字段(rbankj 的 rb_judge/rb_judge_agree)。
                        # 旧臂没有 rec_extra 属性 -> getattr 取 None -> 空 dict,行为逐字不变。
                        rec.update(getattr(self.mem, "rec_extra", None) or {})
                except (ImportError, ModuleNotFoundError, NameError, AttributeError) as e:
                    print(f"FATAL memory-layer config error, killing lane: {e}", flush=True)
                    raise SystemExit(2)   # F17:缺包/缺名=代码配置错,人必须看见,不得静默退化成 seq
                except Exception as e:
                    rec["mem_write_error"] = str(e)[:150]
                # F9 补(P1-4 收窄):哨兵只认"写入异常"连击——合法空 session 的 0 节点不计,
                # 否则难链连空手会假 ChainAbort 死循环
                if self.mem and getattr(self.mem, "llm_writer", False):
                    mem_zero_streak = mem_zero_streak + 1 if rec.get("mem_write_error") else 0
                    if mem_zero_streak >= 3:
                        raise ChainAbort("memory writer errored 3x in a row (endpoint down?)")
                if _LEDGER: _LEDGER.harvest_actor(agent)
                if _LEDGER: rec.update(_LEDGER.session_summary())
                recs.append(rec)
                atomic_results(self.out, recs)
                print(f"  [{self.c['continuous_id']}|{self.arm}] pos{idx} "
                      f"{spec['swebench_instance_id']}: resolved={rec.get('resolved')} "
                      f"steps={rec['n_steps']} inj={rec['injected']} {rec['secs']}s", flush=True)
            if self.mem:
                (self.out / "mem_stats.json").write_text(json.dumps(self.mem.stats()))
            return recs
        finally:
            shutil.rmtree(self.host_root, ignore_errors=True)

    def _blank_rec(self, idx, spec, error):
        # crashed=False:oracle setup 失败是确定性结局(金补丁与基座冲突),计入完成、不触发重跑
        return {"order": idx, "instance": spec["swebench_instance_id"], "arm": self.arm,
                "exit": "setup_error", "secs": 0.0, "wrap_secs": 0.0, "n_steps": 0,
                "n_assistant_msgs": 0, "injected_chars": 0, "injected": False, "map_chars": 0,
                "error": error, "crashed": False, "resolved": None, "gradable": None,
                "f2p": {}, "p2p_failed": [], "p2p_missing": [], "p2p_unscoped": [],
                "collection_errors": [], "error_stage": "setup"}


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--arm", choices=["seq", "oracle", "indep"])
    g.add_argument("--memory-system", choices=sorted(mem_base.REGISTRY))
    ap.add_argument("--chains-file", required=True)
    ap.add_argument("--run-id", default="v2pilot")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--seed-tag", default="s0")
    ap.add_argument("--endpoint-file", default=str(C.ROOT.parent / "w1/vllm_endpoint_next"))
    ap.add_argument("--served-name", default="coder-next")
    ap.add_argument("--step-limit", type=int, default=K.STEP_LIMIT_DEFAULT)
    ap.add_argument("--model", default=None, help="API actor;设后不走本地 endpoint")
    ap.add_argument("--api-key-config", default=None)
    a = ap.parse_args()

    chains = C.load_chains()
    ids = C.load_chain_ids(a.chains_file, a.shard)
    is_mem = a.memory_system is not None
    mode = a.memory_system if is_mem else a.arm
    arm = C.arm_name(mode, is_mem)
    model_name, mk, ep_file = C.model_config(a.model, a.api_key_config, a.served_name, a.endpoint_file)

    done = skipped = 0
    for cid in ids:
        c = chains[cid]
        out = C.out_dir(a.run_id, cid, arm, a.seed_tag)
        if results_complete(out / "results.jsonl", len(c["bug_fixes"])):
            (out / ".claim").unlink(missing_ok=True)
            skipped += 1
            continue
        sif = C.SIF_DIR / (c["docker_image"].split(":")[1] + ".sif")
        if not sif.exists() or sif.stat().st_size < K.SIF_MIN_BYTES:
            print(f"SKIP {cid}: SIF missing/corrupt", flush=True)
            continue
        if not claim_chain(out):
            continue
        hb = Heartbeat(out / ".claim")
        print(f"=== {cid} arm={arm} ({done + skipped + 1}/{len(ids)})", flush=True)
        try:
            ChainRunner(c, arm, mode, out, model_name, mk, a.step_limit, a.run_id).run(ep_file)
            done += 1
            hb.stop()
            (out / ".claim").unlink(missing_ok=True)
        except ChainAbort as e:
            print(f"CHAIN_ABORT {cid} (infra, will retry later): {e}", flush=True)
            hb.stop()
            (out / ".claim").unlink(missing_ok=True)   # 立即放锁,其他 lane 可重试
        except Exception as e:
            print(f"CHAIN_ERROR {cid}: {type(e).__name__}: {str(e)[:200]}", flush=True)
            hb.stop()
            (out / ".claim").unlink(missing_ok=True)   # 不留 4h 路障(P3)
        finally:
            hb.stop()
    print(f"DONE: {done} ran, {skipped} already complete", flush=True)


if __name__ == "__main__":
    main()
