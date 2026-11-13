"""MemOp-prompt 臂(ring-1 guidebook 纯点;预注册预测 P2 的判据臂)。

来源与忠实度(arXiv 2606.05646;github.com/xhguo7/MemOp,MIT):
- system prompt = 官方 memop/construct/prompts.py::MEMORY_PROMPT 逐字
  (prompts/memop_memory_prompt.txt,勿改一字);
- 滚动覆写语义 M_k = W(M_{k-1}, tau_k):单一 guidance 文档,整体重写,非追加;
- 注入按官方约定置于 "## Task Guidance" 标题下(外层仍套统一 MEM_WRAP_HEAD 保臂间可比);
- 输出从官方响应格式 <snippet>...</snippet> 解析;一次矫正重试(F16 同款结构保证)。
声明的偏离(表 caption 须披露):
- writer = 本地 Qwen(零 API 纪律;官方构造用 Claude-4-Sonnet,部署 writer 权重未开源)
  → 诚实标注 "MemOp-prompt (prompt-writer)";
- 官方 hindsight 同任务 A/B 接受过滤不复现:该过滤在耦合设定下因果不成立(§3.1 三步强制),
  其缺席本身即论点证据;
- 前记忆 + 新轨迹的用户消息拼接为最小中性框架(官方未发布该格式化代码)。"""
import json, os, pathlib, re, urllib.request

from ..base import MemoryLayer, WrapResult, MEM_WRAP_HEAD, register

PROMPTS = pathlib.Path(__file__).parent / "prompts"
SNIP_RE = re.compile(r"<snippet>(.*?)</snippet>", re.S)


def distill_memop_guidance(prev, raw_text):
    """官方 MEMORY_PROMPT 蒸馏 guidance。prev=上一版 guidance(覆写演化输入)。"""
    # env 解析在 try 之外:配置错误必须上抛落账,不得静默零写入(rev2-P1 同款)
    api = os.environ.get("RSM_WRITER_API", os.environ["RSM_API_BASE"]).rstrip("/") + "/chat/completions"
    sysp = (PROMPTS / "memop_memory_prompt.txt").read_text()
    dc = int(os.environ.get("RSM_DISTILL_CAP", "18000"))
    h = dc // 2
    m = raw_text if len(raw_text) <= dc else raw_text[:h] + "\n...[middle truncated]...\n" + raw_text[-h:]
    if prev:
        m = (f"Current task guidance from previous sessions:\n<snippet>{prev}</snippet>\n\n"
             f"New coding agent trajectory:\n{m}")
    body = {"model": os.environ.get("RSM_WRITER_MODEL", "coder-next"), "temperature": 0.0,
            "max_tokens": int(os.environ.get("RSM_WRITER_MAXTOK", "800")),
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": m}]}
    if os.environ.get("RSM_OPENAI_STRICT") == "1":
        # 2026-11-12(env 门控,默认关;同 memory/ours/fusednote.py):OpenAI 协议网关(GoodVision SmartRouter、
        # Azure OpenAI)不认 vLLM 专有的 chat_template_kwargs,上游新模型只认 max_completion_tokens / 默认
        # temperature(见 demo/goodvision.md)。本地路径的请求体逐字不变(录制的论文 run 可复现)。
        body.pop("chat_template_kwargs"); body.pop("temperature")
        body["max_completion_tokens"] = body.pop("max_tokens")
    _hdr = {"Content-Type": "application/json"}
    _key = os.environ.get("RSM_WRITER_API_KEY") or os.environ.get("RSM_API_KEY")
    if _key and _key != "EMPTY":
        _hdr["Authorization"] = f"Bearer {_key}"   # 网关鉴权;本地 vLLM 不设 key 时头部不变

    def _valid(t):
        return bool(SNIP_RE.search(t)) and "mswea_bash_command" not in t

    try:
        cap = int(os.environ.get("RSM_NOTE_CAP_MEMOP", "4000"))  # 防御帽;官方 512-token 限在 prompt 内
        for attempt in range(2):
            req = urllib.request.Request(api, json.dumps(body).encode(), dict(_hdr))
            r = json.load(urllib.request.urlopen(req, timeout=300))
            txt = r["choices"][0]["message"]["content"].split("</think>")[-1].strip()
            if _valid(txt):
                g = SNIP_RE.search(txt).group(1).strip()
                # smoke 实测:writer 常无视"从###起头"自带 H2 标题,与注入壳的标题重复 → 剥掉
                g = re.sub(r"^##\s*Task Guidance\s*\n", "", g).strip()
                return g[:cap]
            body["messages"] = body["messages"][:2] + [
                {"role": "assistant", "content": txt[:500]},
                {"role": "user", "content": "INVALID OUTPUT. Do not continue the trajectory. Provide "
                 "ONLY the guidance snippet wrapped in <snippet></snippet> tags."}]
        print(f"MEMOP_DISTILL_INVALID after retry: {txt[:80]!r}", flush=True)
        return ""
    except OSError:
        raise   # 连接类故障上抛 → runner 记 mem_write_error → 零写入哨兵可见
    except Exception as e:
        print(f"MEMOP_DISTILL_ERROR: {type(e).__name__}: {str(e)[:100]}", flush=True)
        return ""


@register
class MemOpPrompt(MemoryLayer):
    name = "memop"
    llm_writer = True   # 连续 mem_write_error 哨兵启用(F9)

    def __init__(self):
        super().__init__()
        self.guidance = ""

    # 2026-11-12:guidance 存进基类 store 的唯一一项 ("guidance", text),使 harness/mem_snapshot.py(RSM_MEM_SAVE=1,
    # demo 的 live run)与 demo/custom_runner.py 的 store.json 能存/恢复它。论文 run 从不读 store(wrap/stats 是
    # MemOp 自己的),请求与注入逐字不变。
    @property
    def guidance(self):
        return self.store.items[-1][1] if self.store.items else ""

    @guidance.setter
    def guidance(self, g):
        self.store.items = [("guidance", g)] if g else []

    def wrap(self, question, env):
        if not self.guidance:
            return WrapResult(question)
        b = "## Task Guidance\n" + self.guidance
        return WrapResult(f"{MEM_WRAP_HEAD}{b}\n</memory_context>\n\n{question}",
                          injected_chars=len(b), injected=True)

    def write(self, spec, material, delta, env):
        g = distill_memop_guidance(self.guidance, material)
        if not g.strip():
            return 0   # 蒸馏无效 → 保留 M_{k-1}(滚动语义下旧版仍有效,不清空)
        self.guidance = g
        self.n_writes += 1
        return 1

    def stats(self):
        return {"n_writes": self.n_writes, "store_chars": len(self.guidance)}
