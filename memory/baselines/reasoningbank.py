"""ReasoningBank 臂(经验蒸馏范式;arXiv 2509.25140,google-research/reasoning-bank,Apache-2.0)。

忠实度:
- 抽取 system prompt = 官方仓库 third_party/src/minisweagent/memory/instruction.py 的
  SWE 变体 SUCCESSFUL_SI / FAILED_SI 逐字(prompts/rb_swe_{succ,fail}.txt);
- 成败分支走官方 SWE 线的 ground-truth 路径(criteria="gt",我们用 grading 真值,无 judge);
- 用户消息 = "**Query:** ...\n\n**Trajectory:**\n" + 步骤拼接(官方格式);
- 输出解析 "# Memory Item" markdown 块,每任务至多 3 条(官方上限);
- 注入前导句逐字(prompts/rb_inject_preamble.txt),条目原样拼接;
- 温度 1.0(官方设置)。
声明的偏离(caption 披露):检索省略——官方是 gemini-embedding top-1 任务级检索,
3 任务链 bank 仅个位数条目,全量注入为其 top-1 的严格超集,FIFO 帽兜底;writer=本地 Qwen
(官方 gemini-2.5-flash);nothink 轨迹无 <think> 段,步骤=assistant 消息原文(格式适配);
注入位置=统一 MEM_WRAP_HEAD 壳内(官方 append 到 system message,壳统一保臂间可比)。
成败标签经 runner 桥 spec["_rsm_resolved"](grade 先于 write)。

2026-09-17 追加 ReasoningBankJudge(name="rbankj"):官方 SWE 线的**默认**成败判定不是 gt,
而是 swebench.py 的 llm_judge_status —— 同一个 writer 模型读题面+轨迹,回 'success'/'fail'。
rbankj 走这条路径(判官提示逐字官方、temperature 0、判定 "success" in resp.lower()),
真值只落进结果记录用来量判官准确率,不进任何分支。原 ReasoningBank(rbank)保持 gt 路径
一字不改,作为同批 oracle 上界。写手侧记账:judge 与 extract 两次调用走同一个 _wlog sink,
逐调用落 <chain_out>/writer_calls.jsonl(vLLM usage 的真 token 数),Writer (K) 由
analysis/bouchet_0908/mem_cost_exact.py 自动把 judge 那次加进去。"""
import json, os, pathlib, re, time, urllib.request

from ..base import MemoryLayer, WrapResult, MEM_WRAP_HEAD, register

PROMPTS = pathlib.Path(__file__).parent / "prompts"
ITEM_RE = re.compile(r"(# Memory Item.*?)(?=\n# Memory Item|\Z)", re.S)
FIFO_ITEMS = int(os.environ.get("RSM_RB_FIFO", "12"))


def _wlog(log, kind, body, resp, txt):
    """写手侧调用台账:judge 与 extract 走**同一个 sink**,Writer (K) 才能一次把两笔都算上。
    log=None 时整段惰性 —— rbank 的调用点不传 log,该臂行为逐字不变。
    只记 vLLM 回的真 token 数(usage,与 actor 同一个 tokenizer)与字符数,不落正文(项目盘紧)。"""
    if not log or not log.get("out_dir"):
        return
    try:
        u = (resp or {}).get("usage") or {}
        rec = {"ts": round(time.time(), 1), "kind": kind, "arm": log.get("arm"),
               "chain": log.get("chain"), "order": log.get("order"),
               "instance": log.get("instance"), "model": body.get("model"),
               "prompt_tokens": u.get("prompt_tokens"),
               "completion_tokens": u.get("completion_tokens"),
               "total_tokens": u.get("total_tokens"),
               "prompt_chars": sum(len(m.get("content") or "") for m in body.get("messages", [])),
               "max_tokens": body.get("max_tokens"), "temperature": body.get("temperature"),
               "resp_chars": len(txt or ""), "resp_head": (txt or "")[:200]}
        with open(pathlib.Path(log["out_dir"]) / "writer_calls.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:      # 台账绝不许弄挂写侧
        print(f"RB_WLOG_ERROR: {type(e).__name__}: {str(e)[:100]}", flush=True)


# 官方 google-research/reasoning-bank 的 SWE 线判官(third_party/src/minisweagent/run/extra/
# swebench.py:llm_judge_status)。两句提示逐字照抄,不得改写。
JUDGE_SYS = "You are a helpful assistant that judges whether the agent successfully completed the task."
JUDGE_USER = ("Task: {task}\n\nTrajectory:\n{trajectory}\n\nDid the agent successfully "
              "complete the task? Answer with 'success' or 'fail' only.")


def judge_status(query, material, log=None):
    """LLM 判官:与抽取**同端点、同模型、同截断口径**的材料,temperature 0,回 success/fail。
    返回 (verdict: bool, err: str|None, usage: dict)。判定规则同官方:"success" in resp.lower()。
    异常处置与 extract_items 对齐:OSError(端点挂)向上抛让 runner 记 mem_write_error 并重跑,
    其余异常记 err 并按官方 else 分支取 fail。"""
    api = os.environ.get("RSM_WRITER_API", os.environ["RSM_API_BASE"]).rstrip("/") + "/chat/completions"
    dc = int(os.environ.get("RSM_DISTILL_CAP", "18000"))
    h = dc // 2
    m = material if len(material) <= dc else material[:h] + "\n...[middle truncated]...\n" + material[-h:]
    body = {"model": os.environ.get("RSM_WRITER_MODEL", "coder-next"), "temperature": 0.0,
            "max_tokens": int(os.environ.get("RSM_RB_JUDGE_MAXTOK", "16")),
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": JUDGE_SYS},
                         {"role": "user", "content": JUDGE_USER.format(task=query, trajectory=m)}]}
    try:
        req = urllib.request.Request(api, json.dumps(body).encode(), {"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=300))
        txt = r["choices"][0]["message"]["content"].split("</think>")[-1].strip()
        _wlog(log, "judge", body, r, txt)
        u = r.get("usage") or {}
        return ("success" in txt.lower()), None, {
            "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
            "text": txt[:60]}
    except OSError:
        raise
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:100]}"
        print(f"RB_JUDGE_ERROR: {err}", flush=True)
        return False, err, {}


def extract_items(resolved, query, material, log=None):
    api = os.environ.get("RSM_WRITER_API", os.environ["RSM_API_BASE"]).rstrip("/") + "/chat/completions"
    sysp = (PROMPTS / ("rb_swe_succ.txt" if resolved else "rb_swe_fail.txt")).read_text()
    dc = int(os.environ.get("RSM_DISTILL_CAP", "18000"))
    h = dc // 2
    m = material if len(material) <= dc else material[:h] + "\n...[middle truncated]...\n" + material[-h:]
    user = f"**Query:** {query}\n\n**Trajectory:**\n{m}"
    body = {"model": os.environ.get("RSM_WRITER_MODEL", "coder-next"), "temperature": 1.0,
            "max_tokens": int(os.environ.get("RSM_WRITER_MAXTOK", "900")),
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": user}]}
    try:
        for attempt in range(2):
            req = urllib.request.Request(api, json.dumps(body).encode(), {"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=300))
            txt = r["choices"][0]["message"]["content"].split("</think>")[-1].strip()
            _wlog(log, "extract", body, r, txt)
            items = [x.strip() for x in ITEM_RE.findall(txt) if "## Content" in x]
            if items and "mswea_bash_command" not in txt:
                return items[:3]   # 官方上限:每任务至多 3 条
            body["messages"] = body["messages"][:2] + [
                {"role": "assistant", "content": txt[:500]},
                {"role": "user", "content": "INVALID OUTPUT. Follow the Output Format exactly: "
                 "markdown blocks each starting with '# Memory Item'."}]
        print(f"RB_EXTRACT_INVALID after retry: {txt[:80]!r}", flush=True)
        return []
    except OSError:
        raise
    except Exception as e:
        print(f"RB_EXTRACT_ERROR: {type(e).__name__}: {str(e)[:100]}", flush=True)
        return []


@register
class ReasoningBank(MemoryLayer):
    name = "rbank"
    llm_writer = True

    def __init__(self):
        super().__init__()
        self.items = []

    def wrap(self, question, env):
        if not self.items:
            return WrapResult(question)
        pre = (PROMPTS / "rb_inject_preamble.txt").read_text()
        b = pre + "\n\n" + "\n\n".join(self.items)
        return WrapResult(f"{MEM_WRAP_HEAD}{b}\n</memory_context>\n\n{question}",
                          injected_chars=len(b), injected=True)

    def write(self, spec, material, delta, env):
        resolved = bool(spec.get("_rsm_resolved"))
        got = extract_items(resolved, spec.get("problem_statement", "")[:2000], material)
        self.items.extend(got)
        self.items = self.items[-FIFO_ITEMS:]
        self.n_writes += 1 if got else 0
        return len(got)

    def stats(self):
        return {"n_writes": self.n_writes, "store_chars": sum(len(i) for i in self.items)}


@register
class ReasoningBankJudge(ReasoningBank):
    """官方 SWE 线的**默认**判定路径:成败标签由 LLM 判官给出(swebench.py llm_judge_status),
    不读 runner 桥进来的隐藏测试真值。判官 = 与抽取同一个端点/模型/截断材料,temperature 0,
    max_tokens 16,判定 "success" in resp.lower()。真值只写进结果记录量判官准确率,
    **不进任何分支**。rbank(gt 路径)一字不改,与本臂同批跑即 oracle 上界。"""
    name = "rbankj"

    def __init__(self):
        super().__init__()
        self.n_judge = self.n_judge_succ = self.n_judge_gt = self.n_judge_agree = 0
        self.rec_extra = {}

    def write(self, spec, material, delta, env):
        self.rec_extra = {}
        log = {"out_dir": getattr(self, "out_dir", None), "arm": self.name,
               "chain": getattr(self, "chain_id", None), "order": getattr(self, "task_idx", None),
               "instance": spec.get("swebench_instance_id")}
        query = spec.get("problem_statement", "")[:2000]
        verdict, jerr, ju = judge_status(query, material, log)
        gt = spec.get("_rsm_resolved")          # 真值:只记录,不参与分支
        agree = None if gt is None else (bool(verdict) == bool(gt))
        self.n_judge += 1
        self.n_judge_succ += 1 if verdict else 0
        if gt is not None:
            self.n_judge_gt += 1
            self.n_judge_agree += 1 if agree else 0
        self.rec_extra = {"rb_judge": bool(verdict), "rb_judge_gt": (None if gt is None else bool(gt)),
                          "rb_judge_agree": agree, "rb_judge_text": ju.get("text"),
                          "rb_judge_ptok": ju.get("prompt_tokens"),
                          "rb_judge_ctok": ju.get("completion_tokens"),
                          "rb_judge_error": jerr}
        print(f"  RB_JUDGE {log['instance']}: judge={'success' if verdict else 'fail'} "
              f"gt={gt} agree={agree} ptok={ju.get('prompt_tokens')}", flush=True)
        got = extract_items(verdict, query, material, log)
        self.items.extend(got)
        self.items = self.items[-FIFO_ITEMS:]
        self.n_writes += 1 if got else 0
        return len(got)

    def stats(self):
        s = super().stats()
        s.update(n_judge=self.n_judge, n_judge_success=self.n_judge_succ,
                 n_judge_with_gt=self.n_judge_gt, n_judge_agree=self.n_judge_agree)
        return s
