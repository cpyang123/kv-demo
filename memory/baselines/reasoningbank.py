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
成败标签经 runner 桥 spec["_rsm_resolved"](grade 先于 write)。"""
import json, os, pathlib, re, urllib.request

from ..base import MemoryLayer, WrapResult, MEM_WRAP_HEAD, register

PROMPTS = pathlib.Path(__file__).parent / "prompts"
ITEM_RE = re.compile(r"(# Memory Item.*?)(?=\n# Memory Item|\Z)", re.S)
FIFO_ITEMS = int(os.environ.get("RSM_RB_FIFO", "12"))


def extract_items(resolved, query, material):
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
