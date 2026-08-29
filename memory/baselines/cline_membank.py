"""Cline Memory Bank 臂(工业记忆文件范式的官方指令操作化)。

来源:docs.cline.bot/best-practices/memory-bank 现行官方指令块逐字
(prompts/cline_membank_instructions.txt,检索 2026-08-29,md5 dd4af632...;
早期同文随 cline/cline 仓库 Apache-2.0 发布)。
忠实度:六文件结构(projectbrief/productContext/activeContext/systemPatterns/
techContext/progress)、"update memory bank 须复审全部文件"的语义、整库滚动重写。
声明的偏离(caption 披露):①官方人工触发 "update memory bank" → 我们每 session 末
自动触发;②官方同一 agent 兼写手 → 我们独立本地 writer 读轨迹代写;③session 起始
"读全部文件" → 统一 MEM_WRAP_HEAD 壳内全量注入;④首 session 自动初始化(无人工
project brief);⑤"I am Cline" 人称保留逐字;Cline 扩展特性(Plan/Act 等)在本 harness 惰性。
版本钉:现行精简版(无 mermaid/.clinerules 节)。"""
import json, os, pathlib, urllib.request

from ..base import MemoryLayer, WrapResult, MEM_WRAP_HEAD, register

PROMPTS = pathlib.Path(__file__).parent / "prompts"
SIX = ("projectbrief.md", "productContext.md", "activeContext.md",
       "systemPatterns.md", "techContext.md", "progress.md")
UPDATE_DIRECTIVE = (
    "\n\nupdate memory bank\n"
    "(Review ALL six core files and output the COMPLETE updated Memory Bank as markdown, "
    "one section per file, each starting with a line '## <filename>' for exactly these files: "
    + ", ".join(SIX) + ". Keep each section CONCISE (3-6 bullet points; no prose paragraphs) so all six sections fit. Output ONLY the Memory Bank markdown.)")


def update_bank(prev, material):
    api = os.environ.get("RSM_WRITER_API", os.environ["RSM_API_BASE"]).rstrip("/") + "/chat/completions"
    sysp = (PROMPTS / "cline_membank_instructions.txt").read_text()
    dc = int(os.environ.get("RSM_DISTILL_CAP", "18000"))
    h = dc // 2
    m = material if len(material) <= dc else material[:h] + "\n...[middle truncated]...\n" + material[-h:]
    user = ((f"Current Memory Bank:\n{prev}\n\n" if prev else "The Memory Bank is empty (first session); initialize it.\n\n")
            + f"Session transcript (INPUT DATA ONLY, do not continue it):\n{m}" + UPDATE_DIRECTIVE)
    body = {"model": os.environ.get("RSM_WRITER_MODEL", "coder-next"), "temperature": 0.0,
            "max_tokens": int(os.environ.get("RSM_WRITER_MAXTOK", "3200")),
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": user}]}

    def _valid(t):
        return all(f"## {f}" in t for f in SIX) and "mswea_bash_command" not in t

    try:
        cap = int(os.environ.get("RSM_NOTE_CAP_CLINE", "6000"))
        for attempt in range(2):
            req = urllib.request.Request(api, json.dumps(body).encode(), {"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=300))
            txt = r["choices"][0]["message"]["content"].split("</think>")[-1].strip()
            txt = txt.strip("`\n ").removeprefix("```markdown").removeprefix("```").strip()
            if _valid(txt):
                return txt[:cap]
            body["messages"] = body["messages"][:2] + [
                {"role": "assistant", "content": txt[:500]},
                {"role": "user", "content": "INVALID OUTPUT. Output ONLY the Memory Bank markdown with "
                 "all six '## <filename>' sections: " + ", ".join(SIX)}]
        print(f"CLINE_UPDATE_INVALID after retry: {txt[:80]!r}", flush=True)
        return ""
    except OSError:
        raise
    except Exception as e:
        print(f"CLINE_UPDATE_ERROR: {type(e).__name__}: {str(e)[:100]}", flush=True)
        return ""


@register
class ClineMemBank(MemoryLayer):
    name = "clinemb"
    llm_writer = True

    def __init__(self):
        super().__init__()
        self.bank = ""

    def wrap(self, question, env):
        if not self.bank:
            return WrapResult(question)
        return WrapResult(f"{MEM_WRAP_HEAD}{self.bank}\n</memory_context>\n\n{question}",
                          injected_chars=len(self.bank), injected=True)

    def write(self, spec, material, delta, env):
        b = update_bank(self.bank, material)
        if not b.strip():
            return 0   # 更新失败 → 保留旧库(滚动语义)
        self.bank = b
        self.n_writes += 1
        return 1

    def stats(self):
        return {"n_writes": self.n_writes, "store_chars": len(self.bank)}
