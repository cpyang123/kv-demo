"""我们的方法:融合便签(fusednote = 27B actor 兼任 writer;trainednote = 独立 writer 端点,
如 SFT ckpt)。模板与输入上限经 env 可换(输入轴/模板族消融复用同一实现)。"""
import json, os, pathlib, urllib.request

from ..base import MemoryLayer, register

PROMPTS = pathlib.Path(__file__).parent / "prompts"


def distill_fused_note(raw_text):
    """按融合模板蒸馏便签。默认 actor 端点(RSM_API_BASE);设 RSM_WRITER_API/RSM_WRITER_MODEL
    时用独立 writer 端点。RSM_WRITER_PROMPT 换模板;RSM_DISTILL_CAP/RSM_WRITER_MAXTOK/
    RSM_NOTE_CAP 供全量输入与 free 模板消融放大。"""
    # env/模板/上限解析放 try 之外:配置错误(缺 RSM_API_BASE、模板名拼错、cap 非整数)
    # 必须上抛进 mem_write_error 落账,不得静默退化成零写入(rev2-P1)
    api = os.environ.get("RSM_WRITER_API", os.environ["RSM_API_BASE"]).rstrip("/") + "/chat/completions"
    sysp = (PROMPTS / os.environ.get("RSM_WRITER_PROMPT", "fused_writer_prompt.txt")).read_text()
    dc = int(os.environ.get("RSM_DISTILL_CAP", "18000"))
    h = dc // 2
    m = raw_text if len(raw_text) <= dc else raw_text[:h] + "\n...[middle truncated]...\n" + raw_text[-h:]
    body = {"model": os.environ.get("RSM_WRITER_MODEL", "coder-next"), "temperature": 0.0,
            "max_tokens": int(os.environ.get("RSM_WRITER_MAXTOK", "900")),
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": m}]}
    def _valid(t):
        """结构校验:防 writer 被轨迹格式带跑续写(实测 14% 废品率的根因)。"""
        t = t.strip()
        return (t.startswith(("DID:", "CONTEXT:", "VERIFY:", "LESSON:"))
                and "mswea_bash_command" not in t and "```" not in t)

    try:
        cap = int(os.environ.get("RSM_NOTE_CAP", "1500"))
        for attempt in range(2):
            req = urllib.request.Request(api, json.dumps(body).encode(), {"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=300))
            txt = r["choices"][0]["message"]["content"].split("</think>")[-1].strip()
            txt = txt.strip("`\n ").removeprefix("```").strip()
            if _valid(txt):
                return txt[:cap]
            # 一次矫正重试:显式点名违规(结构保证,不赌 prompt 自觉)
            body["messages"] = body["messages"][:2] + [
                {"role": "assistant", "content": txt[:500]},
                {"role": "user", "content": "INVALID OUTPUT. Do not continue the transcript or emit "
                 "commands/code fences. Output ONLY note lines, first line starting with 'DID:'."}]
        print(f"FUSED_DISTILL_INVALID after retry: {txt[:80]!r}", flush=True)
        return ""
    except OSError:
        raise   # 连接类故障上抛→runner 记 mem_write_error→零写入哨兵可见(P1-4 配套)
    except Exception as e:
        print(f"FUSED_DISTILL_ERROR: {type(e).__name__}: {str(e)[:100]}", flush=True)
        return ""


@register
class FusedNote(MemoryLayer):
    name = "fusednote"
    llm_writer = True   # runner 据此启用连续零写入哨兵(F9 补)

    def write(self, spec, material, delta, env):
        note = distill_fused_note(material)
        if not note.strip():
            return 0
        self.store.add(spec["swebench_instance_id"], note)
        self.n_writes += 1
        return 1


@register
class TrainedNote(FusedNote):
    """SFT/RL writer 评测臂:同通路,writer 换独立端点(lane 设 RSM_WRITER_API 指 ckpt vLLM)。"""
    name = "trainednote"
