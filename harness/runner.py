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
from memory import base as mem_base                                     # noqa: E402
from memory.attribution import probes as _p                             # noqa: F401,E402 (注册)
from memory.ours import fusednote as _f                                 # noqa: F401,E402 (注册)

INFRA_EXC = ("Timeout", "Connection", "ServiceUnavailable", "InternalServer",
             "APIError", "RateLimit", "BadGateway")


class ChainAbort(Exception):
    """基建故障:中止本链、不写满 results(F9:留待重跑而非固化为已完成失败链)。"""


# ---------- 认领锁(F11/F12):所有臂共用 ----------

def results_complete(rf, n_tasks):
    """完成判定 = 行数达标且逐行 JSON 有效含 order(F8)。"""
    if not rf.exists():
        return False
    lines = [l for l in rf.read_text().splitlines() if l.strip()]
    if len(lines) != n_tasks:
        return False
    try:
        return all("order" in json.loads(l) for l in lines)
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
            os.rename(claim, claim.with_name(f".claim.stale.{os.getpid()}"))   # 原子夺取
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
        self.run_tag = run_id

    # -- 基线臂工作区协议 --
    def reset_workspace(self):
        self.env.raw("git checkout -q -- . && git clean -fdq")

    def apply_golds(self, upto):
        for b in self.tasks[:upto]:
            ok, err = G.apply_patch(self.env, b["patch"], f"gold{b['order']}")
            if not ok:
                return False, err
        return True, ""

    # -- F5:commit 守卫 --
    def _head(self):
        return self.env.raw("git rev-parse HEAD")["output"].strip()

    def _guard_commits(self, base_head, rec):
        head = self._head()
        if head and base_head and head != base_head:
            rec["agent_committed"] = True
            self.env.raw(f"git reset --soft {base_head}")
        return rec

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
        return (raw[:tc] + "\n\nFINAL DIFF:\n" + delta[:K.DIFF_CAP])[:fc]

    def run(self, ep_file):
        recs = []
        base_head = self._head()
        prev_tree = G.tree_snapshot(self.env)
        try:
            for idx, spec in enumerate(self.tasks, 1):
                if self.mode == "indep":
                    self.reset_workspace()
                elif self.mode == "oracle":
                    self.reset_workspace()
                    ok, err = self.apply_golds(idx - 1)
                    if not ok:
                        recs.append(self._blank_rec(idx, spec, error="oracle setup: " + err))
                        atomic_results(self.out, recs)
                        continue
                t_wrap0 = time.time()
                if self.mem:
                    w = self.mem.wrap(spec["problem_statement"], self.env)
                else:
                    w = mem_base.WrapResult(spec["problem_statement"])
                wrap_secs = round(time.time() - t_wrap0, 1)
                mk = dict(self.model_kwargs)
                if ep_file:
                    mk["api_base"] = G.wait_endpoint(ep_file)
                model = LitellmTextbasedModel(model_name=self.model_name, model_kwargs=mk)
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
                    exit_s = f"error:{type(e).__name__}"
                    err = f"{type(e).__name__}: {str(e)[:200]}"
                    if any(t in type(e).__name__ for t in INFRA_EXC):
                        raise ChainAbort(err) from e          # F9:基建故障不固化
                rec = {"order": idx, "instance": spec["swebench_instance_id"], "arm": self.arm,
                       "exit": exit_s, "secs": round(time.time() - t0, 1), "wrap_secs": wrap_secs,
                       "n_steps": agent.n_calls,                       # F6:api_calls 口径
                       "n_assistant_msgs": sum(1 for m in agent.messages if m.get("role") == "assistant"),
                       "injected_chars": w.injected_chars, "injected": w.injected,   # F10 分账
                       "map_chars": w.map_chars,
                       "error": err, "crashed": bool(err)}             # F7:崩溃行显式标记
                self._guard_commits(base_head, rec)                    # F5
                rec.update(G.grade(self.host, self.sif, self.repo_path, self.prefix,
                                   self.tasks, idx, self.out.name, run_tag=self.run_tag))
                # session 边界:真增量 diff(F1)→ 写记忆
                t_add0 = time.time()
                try:
                    cur_tree = G.tree_snapshot(self.env)
                    delta = G.tree_delta(self.env, prev_tree, cur_tree)
                    prev_tree = cur_tree
                    if self.mem:
                        rec["mem_nodes"] = self.mem.write(spec, self._material(agent, delta),
                                                          delta, self.env)
                        rec["mem_write_secs"] = round(time.time() - t_add0, 1)
                except Exception as e:
                    rec["mem_write_error"] = str(e)[:150]
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
        return {"order": idx, "instance": spec["swebench_instance_id"], "arm": self.arm,
                "exit": "setup_error", "secs": 0.0, "wrap_secs": 0.0, "n_steps": 0,
                "n_assistant_msgs": 0, "injected_chars": 0, "injected": False, "map_chars": 0,
                "error": error, "crashed": True, "resolved": None, "gradable": True,
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
            (out / ".claim").unlink(missing_ok=True)
        except ChainAbort as e:
            print(f"CHAIN_ABORT {cid} (infra, will retry later): {e}", flush=True)
        except Exception as e:
            print(f"CHAIN_ERROR {cid}: {type(e).__name__}: {str(e)[:200]}", flush=True)
        finally:
            hb.stop()
    print(f"DONE: {done} ran, {skipped} already complete", flush=True)


if __name__ == "__main__":
    main()
